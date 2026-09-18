from yandex_music import Client


def show_code(code):
    print()
    print("Открой ссылку в браузере:")
    print(code.verification_url)
    print()
    print("Введи код:")
    print(code.user_code)
    print()
    print("После подтверждения вернись в это окно.")


client = Client()
token = client.device_auth(on_code=show_code)

print()
print("YANDEX_MUSIC_TOKEN=" + token.access_token)
print()
print("Скопируй строку выше в файл .env.")
