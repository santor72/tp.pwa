import os
import requests
from dotenv import load_dotenv


BASE_URL = "https://tp.point.online"
TIMEOUT_SECONDS = 20


def get_csrf_token(session: requests.Session) -> str:
  response = session.get(
      f"{BASE_URL}/csrf-token",
      headers={
          "Accept": "application/json, text/plain, */*",
          "Referer": f"{BASE_URL}/login",
      },
      timeout=TIMEOUT_SECONDS,
  )
  response.raise_for_status()

  token = response.json().get("_csrf")
  if not token:
      raise RuntimeError("Сервер не вернул _csrf в ответе /csrf-token")

  return token


def login(session: requests.Session, email: str, password: str) -> dict:
  csrf_token = get_csrf_token(session)

  response = session.post(
      f"{BASE_URL}/api/techportal-user/login",
      json={
          "email": email,
          "password": password,
      },
      headers={
          "Accept": "application/json, text/plain, */*",
          "Content-Type": "application/json",
          "Referer": f"{BASE_URL}/login",
          "X-CSRF-Token": csrf_token,
      },
      timeout=TIMEOUT_SECONDS,
  )

  # Фронтенд при 403 обновляет CSRF и повторяет исходный запрос.
  if response.status_code == 403:
      csrf_token = get_csrf_token(session)
      response = session.post(
          f"{BASE_URL}/api/techportal-user/login",
          json={
              "email": email,
              "password": password,
          },
          headers={
              "Accept": "application/json, text/plain, */*",
              "Content-Type": "application/json",
              "Referer": f"{BASE_URL}/login",
              "X-CSRF-Token": csrf_token,
          },
          timeout=TIMEOUT_SECONDS,
      )

  response.raise_for_status()
  return response.json()


if __name__ == "__main__":
  load_dotenv()  # Читает TP_LOGIN и TP_PASSWORD из .env

  with requests.Session() as session:
      user = login(
          session=session,
          email=os.environ["TP_LOGIN"],
          password=os.environ["TP_PASSWORD"],
      )

  api_token = user["properties"]["token"]

  print("Вход выполнен")
  print("Пользователь:", user["email"])
  print("Роль:", user["status"])
  print("Токен получен:", bool(api_token))
