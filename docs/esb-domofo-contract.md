# Контракт ESB API для страницы «Домофоны»

## Настройки

- Base URL: `ESB_BASE_URL` из `.env`.
- Системный Bearer token: `ESB_BASE_TOKEN` из `.env`.
- Все ответы ESB имеют поле `ok`.
- `ok: true` означает, что ESB выполнил вызов без ошибок.
- `ok: false` означает ошибку вызова; текст для пользователя берётся из
  `reason`, если он передан.
- HTTP `401` означает ошибку системной авторизации ESB.

## Создать нового пользователя домофона

Вызывается после выбора адреса и заполнения формы нового пользователя.

```http
POST /domofon-new-user
Authorization: Bearer <ESB_BASE_TOKEN>
Content-Type: application/json
```

```json
{
  "locid": 4217,
  "field_flat": 143,
  "field_podezd": 2,
  "client_name": "Иванов Иван Иванович",
  "phone": "+79991234567"
}
```

Поле `phone` необязательное. Если номер не заполнен, поле не включается в JSON,
отправляемый в ESB.

Пример ответа:

```json
{
  "ok": true,
  "reason": "Создан пользователь 15600453 с паролем ..."
}
```

## Добавить домофон существующему пользователю

Вызывается, когда специалист ввёл логин клиента и нажал кнопку подключения.

```http
POST /add-domofon-to-user
Authorization: Bearer <ESB_BASE_TOKEN>
Content-Type: application/json
```

```json
{
  "login": "service_login"
}
```

Пример ответа:

```json
{
  "ok": true,
  "reason": "Услуга домофона подключена"
}
```

## Список локаций

Вызывается при переходе к созданию нового пользователя.

```http
GET /locations
Authorization: Bearer <ESB_BASE_TOKEN>
```

Пример ответа:

```json
{
  "ok": true,
  "locations": [
    {
      "rbt_uuid": "8d52cf63-0ddb-431c-b0d2-1171367b8146",
      "locid": 4217,
      "title": "Чеховский МКД г. Чехов, ул. Земская, дом 5",
      "ufCrm11_1771419206": "Московская область, Чехов, микрорайон Губернский, Земская улица, 5|55.168331;37.46396"
    }
  ]
}
```

Если `ufCrm11_1771419206` заполнено и часть строки до первого `|` непустая,
она используется как текст адреса. Иначе используется `title`.

Frontend получает только нормализованные поля `locid` и `loctext`.

## Поиск квартиры в RBT

Вызывается непосредственно перед созданием пользователя.

```http
GET /flat-search?locid=4217&field_flat=143
Authorization: Bearer <ESB_BASE_TOKEN>
```

Пример ответа:

```json
{
  "ok": true,
  "flats": [
    {
      "flatId": 542,
      "houseId": 3,
      "flat": "143",
      "login": "",
      "password": "",
      "sipPassword": "",
      "openCode": ""
    }
  ]
}
```

Правила сценария:

- если `flats` пуст, вызывается `/domofon-new-user`;
- если у всех элементов `flats` поле `login` пустое, вызывается
  `/domofon-new-user`;
- если хотя бы у одного элемента `flats` заполнено поле `login`, backend
  возвращает ошибку `FLAT_ALREADY_ASSIGNED` и не вызывает
  `/domofon-new-user`;
- поля `password`, `sipPassword`, `openCode` и другие внутренние поля RBT не
  передаются во frontend и не журналируются.
