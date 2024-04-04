import asyncio
import io
import json
import os
import aiohttp
import logging

from cachetools import TTLCache
from telegram import (
    InlineQueryResultCachedAudio,
    Update,
)
from telegram.ext import (
    Application,
    InlineQueryHandler,
    ContextTypes,
    CallbackContext,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_BASE_URL = "https://new.akarpov.ru/api/v1/music/song/"
logger = logging.getLogger(__name__)


def store_file_id(slug, file_id):
    file_id_storage[slug] = file_id
    with open("file_ids.json", "w") as f:
        json.dump(file_id_storage, f)


def load_file_ids():
    try:
        with open("file_ids.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


file_id_storage = load_file_ids()
query_cache = TTLCache(maxsize=100, ttl=600)


def error_handler(update: Update, context: CallbackContext) -> None:
    """Log the error and send a message to notify the user."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)


async def fetch_songs_from_api(query):
    """Fetch songs matching the query from the API asynchronously."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API_BASE_URL}?search={query}&page_size=5"
        ) as response:
            if response.status == 200:
                return await response.json()
            else:
                return {"results": []}


async def download_thumbnail(image_url):
    """
    Download the thumbnail image.

    :param image_url: URL to the thumbnail image.
    :return: BytesIO object of the downloaded image or None if download fails.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as response:
            if response.status == 200:
                image_data = await response.read()
                thumbnail_io = io.BytesIO(image_data)
                return thumbnail_io
            else:
                print("Failed to download thumbnail.")
                return None


async def get_telegram_file_id(song_data, context: ContextTypes.DEFAULT_TYPE):
    """
    Enhanced function to send a song with as much metadata as possible to Telegram,
    and return the 'file_id' for future use.

    :param song_data: Dictionary containing song details.
    :param context: Context from the telegram.ext callback.
    :return: file_id for the Telegram file.
    """
    slug = song_data["slug"]
    if slug in file_id_storage:
        return file_id_storage[slug]

    # Extracting song metadata
    file_url = song_data["file"]
    duration = song_data.get("length", 0)
    performer = ", ".join([author["name"] for author in song_data.get("authors", [])])
    title = song_data.get("name", "Unknown Title")
    album_slug = song_data.get("album", {}).get("slug", "")
    caption = f"https://next.akarpov.ru/music/albums/{album_slug}#{slug}"
    thumbnail_url = song_data.get("image_cropped", None)

    async with aiohttp.ClientSession() as session:
        async with session.get(file_url) as response:
            if response.status == 200:
                file_content = await response.read()
                file_io = io.BytesIO(file_content)
                file_io.name = slug + ".mp3"

                if thumbnail_url:
                    thumbnail_io = await download_thumbnail(thumbnail_url)
                else:
                    thumbnail_io = None
                if thumbnail_io:
                    message = await context.bot.send_audio(
                        chat_id="868474142",  # private chat ID
                        audio=file_io,
                        duration=duration,
                        performer=performer,
                        title=title,
                        caption=caption,
                        thumbnail=thumbnail_io,
                    )
                else:
                    message = await context.bot.send_audio(
                        chat_id="868474142",
                        audio=file_io,
                        duration=duration,
                        performer=performer,
                        title=title,
                        caption=caption,
                    )

                # Saving and returning file_id
                file_id = message.audio.file_id
                file_id_storage[slug] = file_id

                # Optionally, save to file for persistence
                with open("file_ids.json", "w") as f:
                    json.dump(file_id_storage, f)

                return file_id
            else:
                print("Failed to download file.")
                return None


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()

    # Check cache first
    if query in query_cache:
        results = query_cache[query]
    else:
        songs = await fetch_songs_from_api(query)
        results = []
        for song in songs["results"]:
            file_id = await get_telegram_file_id(song, context)
            if file_id:
                results.append(
                    InlineQueryResultCachedAudio(
                        id=song["slug"],
                        audio_file_id=file_id,
                        caption=f"https://next.akarpov.ru/music/albums/{song['album']['slug']}#{song['slug']}"
                    )
                )
        # Store results in cache
        query_cache[query] = results

    await context.bot.answer_inline_query(update.inline_query.id, results, cache_time=1)


async def fetch_all_songs_from_api():
    """Fetch all songs from the API, handling pagination."""
    all_songs = []
    next_page_url = f"{API_BASE_URL}?page_size=1000"

    async with aiohttp.ClientSession() as session:
        while next_page_url:
            print(f"Fetching songs from: {next_page_url}")
            async with session.get(next_page_url) as response:
                if response.status == 200:
                    data = await response.json()
                    all_songs.extend(data["results"])
                    next_page_url = data.get("next")
                else:
                    logger.error("Failed to fetch songs from API.")
                    break

    return all_songs


async def upload_songs(context: ContextTypes.DEFAULT_TYPE):
    songs = await fetch_all_songs_from_api()
    if not songs:
        logger.info("No songs found in the API to upload.")
        return

    total_songs = len(songs)

    for i, song in enumerate(songs):
        slug = song["slug"]
        if slug not in file_id_storage:
            print(f"Uploading song: {slug}", f"{i}/{total_songs}")
            file_id = await get_telegram_file_id(song, context)
            if file_id:
                store_file_id(slug, file_id)
                print(f"Uploaded and stored: {slug}")
            else:
                print(f"Failed to upload song: {slug}")
        else:
            print(f"Song already uploaded: {slug}")


def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(InlineQueryHandler(inline_query))
    application.add_error_handler(error_handler)

    # Start uploading songs after bot setup
    loop = asyncio.get_event_loop()
    context = ContextTypes.DEFAULT_TYPE(application, loop)
    if os.getenv("UPLOAD_SONGS") == "true":
        print("Uploading songs...")
        loop.run_until_complete(upload_songs(context))

    application.run_polling()


if __name__ == "__main__":
    main()
