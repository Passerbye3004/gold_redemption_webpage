import requests
def send_message(token,chat_id,text):

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    params = {"chat_id": chat_id, "text": text}

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
    except Exception as e:
        print(f"Telegram send error: {e}")
