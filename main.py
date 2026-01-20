from telethon import TelegramClient, events, functions
from telethon.sessions import StringSession
import sqlite3
import time
import pickle
import os
import traceback
import requests
import random
import asyncio
import pytz
from datetime import datetime
from duckduckgo_search import DDGS
from flask import Flask
from threading import Thread

def get_db_connection():
    return sqlite3.connect("bot_data.db", timeout=20)

# Теперь мы берем данные из Environment Variables, которые ты ввел в панели Koyeb
# 1. Получаем данные из настроек Koyeb (Environment Variables)
# Используем одинаковые имена переменных везде
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
STRING_SESSION = os.environ.get("STRING_SESSION")

# 2. Проверка на наличие данных
if not all([API_ID, API_HASH, STRING_SESSION]):
    print("❌ ОШИБКА: Одна из переменных (API_ID, API_HASH, STRING_SESSION) не задана в настройках Koyeb!")
    exit(1)

try:
    # Важно: API_ID должен быть числом, поэтому используем int()
    api_id_int = int(API_ID)
    
    # 3. Инициализация клиента
    client = TelegramClient(StringSession(STRING_SESSION), api_id_int, API_HASH)
    print("✅ Клиент успешно инициализирован")
except ValueError:
    print("❌ ОШИБКА: API_ID должен содержать только цифры!")
    exit(1)
except Exception as e:
    print(f"❌ Ошибка инициализации сессии: {e}")
    exit(1)

client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

# Инициализация клиента через StringSession
try:
    client = TelegramClient(StringSession(STRING_SESSION), api_id, api_hash)
except Exception as e:
    print("❌ Ошибка инициализации сессии! Проверь, правильно ли скопирована строка.")
    print(f"Ошибка: {e}")
    exit()

VERSION = "0.1 beta"
DB_NAME = "bot_data.db"
status_task = None

# --- БАЗА ДАННЫХ ---
def init_db():
    try:
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS templates (id TEXT PRIMARY KEY, text TEXT, media_id BLOB)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS trusted_users (user_id INTEGER PRIMARY KEY)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS bot_users (user_id INTEGER PRIMARY KEY)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS bot_premium (user_id INTEGER PRIMARY KEY)''')

        # Дефолтные настройки
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('main_prefix', '.с')")
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('pp_prefix', 'нн')")
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('pp_enabled', '1')")
        conn.commit(); conn.close()
    except Exception as e: print(f"Ошибка БД: {e}")

init_db()

def get_config(key):
    try:
        conn = sqlite3.connect(DB_NAME)
        res = conn.cursor().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        return res[0] if res else None
    except: return None

def set_config(key, value):
    conn = sqlite3.connect(DB_NAME)
    conn.cursor().execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit(); conn.close()

# --- ЛОГИКА АВТОСТАТУСА ---
def get_clock_emoji():
    clocks = ["🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚"]
    hour = datetime.now(pytz.timezone('Europe/Moscow')).hour % 12
    return clocks[hour]

async def status_loop(text_template):
    while True:
        try:
            msk_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime("%H:%M")
            emoji = get_clock_emoji()
            final_status = text_template.replace("{time}", f"{msk_time}{emoji}")
            await client(functions.account.UpdateProfileRequest(about=final_status))
            await asyncio.sleep(60)
        except: await asyncio.sleep(10)

# =======================================================
#               ГЛАВНЫЙ ПРОЦЕССОР КОМАНД
#  Вся логика теперь тут. Вызываем её из handler и repeater.
# =======================================================
async def command_processor(event, text):
    try:
        # 1. Проверяем префикс (например .с)
        main_prefix = (get_config('main_prefix') or ".с").lower()
        if not text.lower().startswith(main_prefix):
            return # Не команда

        # Отрезаем префикс: ".с пинг" -> "пинг"
        text_body = text[len(main_prefix):].strip()
        args = text_body.split()
        if not args: return
        command = args[0].lower()

        # --- [1] ШАБЛОНЫ (БЫСТРЫЙ ВЫЗОВ) ---
        if text.lower().startswith(f"{main_prefix} шаб "):
            query = text[len(main_prefix)+5:].strip().lower()
            conn = sqlite3.connect(DB_NAME)
            if query.isdigit():
                res = conn.cursor().execute("SELECT text, media_id FROM (SELECT rowid as num, text, media_id FROM templates) WHERE num = ?", (query,)).fetchone()
            else:
                res = conn.cursor().execute("SELECT text, media_id FROM templates WHERE id = ?", (query,)).fetchone()
            conn.close()

            if res:
                t, m_blob = res
                await event.delete()
                if m_blob:
                    try: await client.send_file(event.chat_id, pickle.loads(m_blob), caption=t)
                    except: await client.send_message(event.chat_id, t)
                else: await client.send_message(event.chat_id, t)
            return

        # --- [2] ОСНОВНЫЕ КОМАНДЫ ---

        if command == "пинг":
            start = time.perf_counter()
            await event.edit("🏓 Pong!")
            ms = (time.perf_counter() - start) * 1000
            await event.edit(f"🏓 Pong! `{ms:.2f}ms`")

        elif command == "уд":
            if len(args) < 2: return
            if args[1].isdigit():
                count = int(args[1])
                await event.delete()
                async for msg in client.iter_messages(event.chat_id, limit=count):
                    await msg.delete()

        # --- ИНФО И СПИСКИ ---
        # --- ИНФО ---
        elif command == "инфо":
            conn = get_db_connection()
            # Получаем префикс из базы, чтобы не было ошибки NameError
            cur_p = conn.cursor().execute("SELECT value FROM settings WHERE key='main_prefix'").fetchone()[0]
            conn.close()

            info_text = (
                f"🛠 **UserBot Helper**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ **Статус:** `Работает`\n"
                f"📌 **Версия:** `{VERSION}`\n"
                f"⚙️ **Префикс:** `{cur_p}`\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
            )
            await event.edit(info_text)

        elif command == "доверяю":
            await event.edit("🔍 *Загружаю список доверенных...*")
            conn = sqlite3.connect(DB_NAME)
            rows = conn.cursor().execute("SELECT user_id FROM trusted_users").fetchall()
            conn.close()

            if rows:
                msg = "**👥 Доверенные (для повторялки):**\n"
                for r in rows:
                    uid = r[0]
                    try:
                        # Пытаемся получить информацию о пользователе
                        user = await client.get_entity(uid)
                        name = user.first_name
                        if user.last_name:
                            name += f" {user.last_name}"
                        msg += f"• [{name}](tg://user?id={uid}) (`{uid}`)\n"
                    except Exception:
                        # Если бот никогда не видел этого юзера, пишем просто ID
                        msg += f"• Неизвестный юзер (`{uid}`)\n"
            else:
                msg = "📂 Список доверенных пуст."

            await event.edit(msg)

        elif command in ["+юзер", "-юзер", "+прем", "-прем", "+дов", "-дов"]:
            reply = await event.get_reply_message()
            user_id = None
            if reply: user_id = reply.sender_id
            elif len(args) > 1:
                try:
                    u = await client.get_entity(args[1])
                    user_id = u.id
                except: pass

            if not user_id:
                await event.edit("❌ Кому? (реплай или ID)")
                return

            if "юзер" in command: table = "bot_users"; desc = "Юзеры"
            elif "прем" in command: table = "bot_premium"; desc = "Премиум"
            else: table = "trusted_users"; desc = "Доверенные"

            conn = sqlite3.connect(DB_NAME)
            if "+" in command:
                conn.cursor().execute(f"INSERT OR IGNORE INTO {table} (user_id) VALUES (?)", (user_id,))
                await event.edit(f"✅ `{user_id}` добавлен в {desc}.")
            else:
                conn.cursor().execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
                await event.edit(f"🗑 `{user_id}` удален из {desc}.")
            conn.commit(); conn.close()

        elif command == "кто":
            target = None
            reply = await event.get_reply_message()
            try:
                if reply: target = await client.get_entity(reply.sender_id)
                elif len(args) > 1: target = await client.get_entity(args[1])
                else: target = await client.get_entity(event.chat_id)
            except Exception as e:
                await event.edit(f"❌ Ошибка: {e}"); return

            if not hasattr(target, 'first_name'):
                await event.edit("❌ Это не пользователь."); return

            conn = sqlite3.connect(DB_NAME)
            is_bot_user = conn.cursor().execute("SELECT 1 FROM bot_users WHERE user_id = ?", (target.id,)).fetchone()
            is_bot_prem = conn.cursor().execute("SELECT 1 FROM bot_premium WHERE user_id = ?", (target.id,)).fetchone()
            conn.close()

            info = (
                f"👤 **{target.first_name}**\n"
                f"🆔 `{target.id}`\n"
                f"🔗 @{target.username if target.username else 'нет'}\n"
                f"⚙️ Бот: {'✅' if is_bot_user else '❌'}\n"
                f"💎 Прем: {'✅' if is_bot_prem else '❌'}"
                   )
            await event.edit(info)

 # --- GPT / AI ---
        elif command == "гпт":
            if len(args) < 2:
                await event.edit("❌ **Напиши вопрос!**")
                return

            prompt = " ".join(args[1:])
            await event.edit("🧠 **Ищу свободную нейросеть...**")

            try:
                import g4f

                # Запускаем поиск ответа через провайдеров, которые сейчас в сети
                response = g4f.ChatCompletion.create(
                    model=g4f.models.gpt_4,  # Пытаемся вызвать четверку
                    messages=[{"role": "user", "content": prompt}],
                )

                if response:
                    await event.edit(f"🤖 **GPT:**\n\n{response[:4000]}")
                else:
                    await event.edit("❌ Все провайдеры заняты. Попробуй через минуту.")

            except Exception as e:
                print(f"G4F Error: {e}")
                # Если G4F не справился, используем самый простой текстовый эндпоинт
                await event.edit("⚠️ Пробую резервный канал...")
                try:
                    res = requests.get(f"https://darkness.ashlynn.workers.dev/chat?prompt={prompt}", timeout=15)
                    await event.edit(f"🤖 **GPT (Резерв):**\n\n{res.text}")
                except:
                    await event.edit(f"⚠️ Ошибка связи: `{e}`")



        # --- КАРТИНКИ (GELBOORU / NEKO) ---
        elif command == "nsfw":
            query = " ".join(args[1:]) if len(args) > 1 else "rating:explicit"
            await event.edit(f"🔞 **Ищу в архиве...**")

            try:
                # Поиск по тегам на yande.re (один из самых открытых ресурсов)
                # Добавляем rating:explicit для 18+
                url = f"https://yande.re/post.json?tags={query}+rating:explicit&limit=20"
                res = requests.get(url, timeout=10).json()

                if res:
                    # Выбираем случайный пост из найденных
                    post = random.choice(res)
                    img_url = post.get('file_url')

                    await event.delete()
                    await client.send_file(event.chat_id, img_url, caption=f"🔞 **NSFW:** `{query}`")
                else:
                    await event.edit("❌ Ничего не найдено по этому тегу.")
            except Exception as e:
                await event.edit(f"⚠️ Ошибка: `{e}`")

        elif command in ["неко", "кицуне", "лиса"]:
            await event.edit("🐾 ...")
            try:
                if "лиса" in command:
                    url = requests.get("https://randomfox.ca/floof/").json()['image']
                elif "киц" in command:
                    url = requests.get("https://nekos.best/api/v2/kitsune").json()['results'][0]['url']
                else:
                    url = requests.get("https://waifu.pics/api/sfw/neko").json()['url']

                await event.delete()
                await client.send_file(event.chat_id, url)
            except Exception as e: await event.edit(f"❌ Err: {e}")

        # --- [3] УПРАВЛЕНИЕ ШАБЛОНАМИ (ДОПОЛНЕНИЕ) ---

        # Список всех шаблонов
        elif command == "шабы":
            conn = sqlite3.connect(DB_NAME)
            rows = conn.cursor().execute("SELECT rowid, id FROM templates").fetchall()
            conn.close()
            if rows:
                msg = "**📂 Ваши шаблоны:**\n" + "\n".join([f"{r[0]}. `{r[1]}`" for r in rows])
            else:
                msg = "📂 Список шаблонов пуст."
            await event.edit(msg)

        # Cоздание шаблона
        elif command == "+шаб":
            if len(args) < 2:
                await event.edit("❌ Имя?: .с +шаб тест")
                return
            name = args[1].lower()
            reply = await event.get_reply_message()
            media_blob = None
            content = ""

            if reply:
                content = reply.text or ""
                if reply.media: media_blob = pickle.dumps(reply.media)
            else:
                lines = raw_text.split('\n')
                if len(lines) > 1 or event.media:
                    content = "\n".join(lines[1:])
                    if event.media: media_blob = pickle.dumps(event.media)
                else:
                    await event.edit("❌ Нет текста/медиа.")
                    return

            conn = sqlite3.connect(DB_NAME)
            conn.cursor().execute("INSERT OR REPLACE INTO templates (id, text, media_id) VALUES (?, ?, ?)",
                                  (name, content, media_blob))
            conn.commit();
            conn.close()
            await event.edit(f"✅ Шаблон {name} сохранен.")

        # Удаление шаблона: .с -шаб имя
        elif command == "-шаб":
            if len(args) < 2:
                await event.edit("❌ Укажите имя шаблона: `.с -шаб тест`")
                return
            name = args[1].lower()
            conn = sqlite3.connect(DB_NAME)
            # Проверяем, существует ли он
            exists = conn.cursor().execute("SELECT 1 FROM templates WHERE id = ?", (name,)).fetchone()
            if exists:
                conn.cursor().execute("DELETE FROM templates WHERE id = ?", (name,))
                conn.commit()
                await event.edit(f"🗑 Шаблон `{name}` удален.")
            else:
                await event.edit(f"❌ Шаблон `{name}` не найден.")
            conn.close()

        # --- АВТОСТАТУС ---
        elif command == "автостатус":
            tpl = " ".join(args[1:])
            set_config('status_template', tpl)
            await event.edit(f"✅ Template: `{tpl}`")
        elif command == "+автостатус":
            global status_task
            tpl = get_config('status_template')
            if tpl:
                if status_task: status_task.cancel()
                status_task = asyncio.create_task(status_loop(tpl))
                set_config('status_enabled', '1')
                await event.edit("✅ ON")
            else: await event.edit("❌ Set text first.")
        elif command == "-автостатус":
            if status_task: status_task.cancel()
            set_config('status_enabled', '0')
            await client(functions.account.UpdateProfileRequest(about=""))
            await event.edit("❌ OFF")

        # --- НАСТРОЙКИ ПОВТОРЯЛКИ ---
        elif command == "+пп":
            set_config('pp_enabled', '1')
            await event.edit("✅ ПП ВКЛ")
        elif command == "-пп":
            set_config('pp_enabled', '0')
            await event.edit("❌ ПП ВЫКЛ")

        # ПОИСК КАРТИНОК
        elif command in ["поиск", "img"]:
            if len(args) < 2:
                await event.edit("❌ **Что искать?**")
                return

            query = " ".join(args[1:])
            await event.edit(f"🔍 **Глобальный поиск:** `{query}`...")

            try:
                # Используем Bing через подмену User-Agent для получения реальных ссылок
                search_url = f"https://www.bing.com/images/search?q={requests.utils.quote(query)}&form=HDRSC2&first=1"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"}

                response = requests.get(search_url, headers=headers, timeout=10)
                import re

                # Извлекаем прямые ссылки на изображения из атрибутов murl (Media URL)
                links = re.findall(r'murl&quot;:&quot;(https://.*?\.(?:jpg|jpeg|png|webp|gif))&quot;', response.text)

                if links:
                    # Берем случайную из первых 15 найденных (чтобы не всегда первую)
                    img_url = random.choice(links[:15])

                    # Скачиваем в память
                    img_res = requests.get(img_url, timeout=10)
                    from io import BytesIO
                    image_stream = BytesIO(img_res.content)
                    image_stream.name = 'photo.jpg'

                    await event.delete()
                    await client.send_file(
                        event.chat_id,
                        image_stream,
                        caption=f"🔎 **Найдено в сети:** `{query}`",
                        force_document=False
                    )
                else:
                    await event.edit(f"❌ Ничего не найдено по запросу `{query}`")
            except Exception as e:
                # ВОТ ЭТОГО БЛОКА У ВАС НЕ ХВАТАЛО
                await event.edit(f"❌ Ошибка: {e}")

         # --- СМЕНА ОСНОВНОГО ПРЕФИКСА: .с префикс [знак] ---
        elif command in ["преф", "префикс"]:
            if len(args) < 2:
                await event.edit("❌ Укажите новый префикс (например: `.с преф !`)")
                return

            new_prefix = args[1]
            conn = get_db_connection()
            # Обновляем значение в базе данных
            conn.cursor().execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('main_prefix', ?)",
                                  (new_prefix,))
            conn.commit()
            conn.close()
            await event.edit(f"✅ Новый префикс установлен: `{new_prefix}`")

        # --- СМЕНА ПРЕФИКСА ПОВТОРЯЛКИ: .с пп [знак] ---
        elif command == "пп" and len(args) > 1:
            new_pp = args[1]
            conn = get_db_connection()
            conn.cursor().execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('pp_prefix', ?)", (new_pp,))
            conn.commit()
            conn.close()
            await event.edit(f"✅ Префикс повторялки изменен на: `{new_pp}`")

        # --- КОНЕЦ КОМАНД ---



    except Exception as e:
        print(f"Processor Error: {traceback.format_exc()}")
        try:
            await event.edit(f"⚠️ Error: {e}")
        except:
            pass
# ================= ХЕНДЛЕРЫ =================

# 1. ОБЫЧНЫЕ КОМАНДЫ (ОТ МЕНЯ)
@client.on(events.NewMessage(outgoing=True))
async def main_handler(event):
    # Просто передаем сообщение в процессор
    await command_processor(event, event.raw_text)

# 2. ПОВТОРЯЛКА (ОТ ДОВЕРЕННЫХ)
@client.on(events.NewMessage(incoming=True))
async def repeater(event):
    if get_config('pp_enabled') != '1': return
    pp_prefix = get_config('pp_prefix') # "нн"

    # Если сообщение начинается с "нн"
    if pp_prefix and event.raw_text.lower().startswith(pp_prefix.lower()):
        # Проверяем доверие
        conn = sqlite3.connect(DB_NAME)
        is_trusted = conn.cursor().execute("SELECT 1 FROM trusted_users WHERE user_id = ?", (event.sender_id,)).fetchone()
        conn.close()

        if is_trusted:
            # Текст без "нн" (например ".с пинг")
            clean_text = event.raw_text[len(pp_prefix):].strip()

            # 1. Бот отправляет сообщение от вашего имени
            my_msg = await event.respond(clean_text, reply_to=event.reply_to_msg_id)

            # 2. ПРОВЕРКА НА КОМАНДУ
            # Теперь мы берем отправленное сообщение (my_msg) и передаем его в процессор
            # Процессор подумает, что это вы написали команду, и выполнит её
            main_prefix = (get_config('main_prefix') or ".с").lower()
            if clean_text.lower().startswith(main_prefix):
                await command_processor(my_msg, clean_text)

app = Flask('')

@app.route('/')
def home():
    return "I am alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()
async def main():
    await client.start()
    keep_alive()  # Теперь функция определена, и ошибки не будет
    await client.run_until_disconnected()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())

print(f"Бот {VERSION} запущен.")
client.start()
client.run_until_disconnected()
