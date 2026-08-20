import os
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from caldav import DAVClient
from icalendar import Calendar, Event
from datetime import datetime, timedelta, timezone
import uuid
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# --- КОНФИГУРАЦИЯ ---
YANDEX_LOGIN = os.getenv('YANDEX_LOGIN')
YANDEX_APP_PASSWORD = os.getenv('YANDEX_APP_PASSWORD')
CALENDAR_ID = os.getenv('CALENDAR_ID', 'events-38315911')
CALDAV_URL = "https://caldav.yandex.ru"
WORK_START = 9   # 9:00
WORK_END = 18    # 18:00
SLOT_DURATION = 60  # минут

def get_calendar():
    """Подключается к Яндекс.Календарю и возвращает объект календаря."""
    try:
        client = DAVClient(
            url=CALDAV_URL,
            username=YANDEX_LOGIN,
            password=YANDEX_APP_PASSWORD
        )
        principal = client.principal()
        calendars = principal.calendars()
        for cal in calendars:
            if CALENDAR_ID in cal.id:
                return cal, client, principal
        if calendars:
            print(f"⚠️ Календарь '{CALENDAR_ID}' не найден, использую первый: {calendars[0].id}")
            return calendars[0], client, principal
        return None, None, None
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        return None, None, None

def fetch_event_data(event_url, login, password):
    """Скачивает содержимое события по URL с авторизацией."""
    session = requests.Session()
    session.auth = HTTPBasicAuth(login, password)
    try:
        response = session.get(event_url)
        if response.status_code == 200:
            return response.text
        else:
            print(f"⚠️ Ошибка загрузки события {event_url}: {response.status_code}")
            return None
    except Exception as e:
        print(f"⚠️ Исключение при загрузке: {e}")
        return None

def parse_ical_data(ical_str):
    """Парсит iCalendar-строку и возвращает список событий."""
    if not ical_str:
        return []
    
    # Если строка содержит XML, извлекаем iCalendar
    if '<C:calendar-data>' in ical_str:
        import re
        match = re.search(r'<C:calendar-data>(.*?)</C:calendar-data>', ical_str, re.DOTALL)
        if match:
            ical_str = match.group(1).strip()
    
    try:
        cal = Calendar.from_ical(ical_str)
        events = []
        for component in cal.walk():
            if component.name == "VEVENT":
                summary = component.get('summary', 'Без названия')
                dtstart = component.get('dtstart').dt
                dtend = component.get('dtend').dt
                events.append({
                    'summary': str(summary),
                    'start': dtstart,
                    'end': dtend
                })
        return events
    except Exception as e:
        print(f"⚠️ Ошибка парсинга iCalendar: {e}")
        return []

@app.route('/slots', methods=['GET'])
def get_free_slots():
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({"error": "Дата не указана"}), 400
    
    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"error": "Неверный формат даты"}), 400

    today = datetime.now().date()
    if date < today:
        return jsonify({"error": "Нельзя выбрать прошедшую дату"}), 400

    calendar_obj, client, principal = get_calendar()
    if not calendar_obj:
        return jsonify({"error": "Календарь не найден"}), 500

    start_dt = datetime(date.year, date.month, date.day, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=timezone.utc)

    print(f"🔍 Поиск событий с {start_dt} по {end_dt}")

    try:
        # Получаем список событий (URL) за день
        events = calendar_obj.date_search(start=start_dt, end=end_dt, expand=True)
        print(f"📅 Найдено событий: {len(events)}")
    except Exception as e:
        print(f"❌ Ошибка поиска: {e}")
        return jsonify({"error": f"Ошибка получения событий: {str(e)}"}), 500

    busy = []
    login = YANDEX_LOGIN
    password = YANDEX_APP_PASSWORD

    for idx, event_item in enumerate(events):
        # Извлекаем URL события
        event_url = None
        if hasattr(event_item, 'url'):
            event_url = event_item.url
        elif isinstance(event_item, str) and event_item.startswith('http'):
            event_url = event_item
        
        if not event_url:
            print(f"⚠️ Событие {idx+1}: не удалось получить URL")
            continue

        print(f"📎 Событие {idx+1}: загрузка {event_url}")
        
        # Скачиваем содержимое события
        ical_data = fetch_event_data(event_url, login, password)
        if not ical_data:
            continue

        # Парсим iCalendar
        parsed_events = parse_ical_data(ical_data)
        for ev in parsed_events:
            # Приводим время к UTC
            start = ev['start']
            end = ev['end']
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            busy.append((start, end))
            print(f"   Занято: {start} – {end}")

    # Генерируем свободные слоты
    slots = []
    start_slot = datetime(date.year, date.month, date.day, WORK_START, 0, 0, tzinfo=timezone.utc)
    end_slot = datetime(date.year, date.month, date.day, WORK_END, 0, 0, tzinfo=timezone.utc)
    
    current = start_slot
    while current + timedelta(minutes=SLOT_DURATION) <= end_slot:
        slot_start = current
        slot_end = current + timedelta(minutes=SLOT_DURATION)
        is_free = True
        for busy_start, busy_end in busy:
            if not (slot_end <= busy_start or slot_start >= busy_end):
                is_free = False
                break
        if is_free:
            slots.append(slot_start.strftime('%H:%M'))
        current += timedelta(minutes=SLOT_DURATION)

    print(f"✅ Свободных слотов: {len(slots)}")
    return jsonify(slots)

@app.route('/book', methods=['POST'])
def book_appointment():
    data = request.json
    patient_name = data.get('name')
    patient_phone = data.get('phone')
    patient_email = data.get('email')
    start_time_str = data.get('start_time')

    if not patient_name or not patient_phone or not start_time_str:
        return jsonify({"error": "Не хватает данных"}), 400

    try:
        start_time = datetime.fromisoformat(start_time_str).astimezone(timezone.utc)
        end_time = start_time + timedelta(minutes=SLOT_DURATION)

        calendar_obj, client, principal = get_calendar()
        if not calendar_obj:
            return jsonify({"error": "Календарь не найден"}), 500

        # Проверяем занятость (снова, как в /slots)
        date = start_time.date()
        start_dt = datetime(date.year, date.month, date.day, 0, 0, 0, tzinfo=timezone.utc)
        end_dt = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=timezone.utc)

        events = calendar_obj.date_search(start=start_dt, end=end_dt, expand=True)
        login = YANDEX_LOGIN
        password = YANDEX_APP_PASSWORD

        for event_item in events:
            event_url = None
            if hasattr(event_item, 'url'):
                event_url = event_item.url
            elif isinstance(event_item, str) and event_item.startswith('http'):
                event_url = event_item
            if not event_url:
                continue
            
            ical_data = fetch_event_data(event_url, login, password)
            if not ical_data:
                continue
            
            parsed_events = parse_ical_data(ical_data)
            for ev in parsed_events:
                start = ev['start']
                end = ev['end']
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                if not (end_time <= start or start_time >= end):
                    return jsonify({"error": "Это время уже занято"}), 409

        # Создаём событие в календаре
        cal = Calendar()
        cal.add('prodid', '-//My Calendar//')
        cal.add('version', '2.0')

        event = Event()
        event.add('uid', str(uuid.uuid4()))
        event.add('dtstamp', datetime.now(timezone.utc))
        event.add('dtstart', start_time)
        event.add('dtend', end_time)
        event.add('summary', f'Консультация: {patient_name}')
        event.add('description', f'Телефон: {patient_phone}\nEmail: {patient_email or "Не указан"}')
        event.add('location', 'г. Екатеринбург, ул. Примерная, д. 1 (или онлайн)')

        cal.add_component(event)
        ics_data = cal.to_ical().decode('utf-8')

        calendar_obj.save_event(ics_data)

        return jsonify({"success": True, "message": "Запись успешно создана!"}), 200

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)