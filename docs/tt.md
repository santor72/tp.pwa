# Общее
При авторизации от Техпортала приходит ответ в том числе содержащий поле id - это идентификатор пользователя, его храним в redis
Пользователю показываем только  заявки в которых он указан исполнителем
Для получения заявок запрашиваем Техпортал фильтруя по id пользователя
# Интерфейс
Карточки заявок занимают всю ширину
# Заявки делятся
на два типа
 - Новое подключение
 - Ремонт
На два состояния
 - Исполнена
 - Не исполнена
## Цветовая кодировка
Не исполнена Новое подключение - белый цвет
Не исполнена Ремонт - слабо розовый с градиентом от центра к краям
Исполненные - слабо-зеленый с градиентом от центра к краям
## Поведение
При коротком тапе открываем подробную информацию о заявке
Долгий тап по карточке неисполненной завки отмечает ее как исполненную
Долгий тап по карточке исполненной завки отмечает ее как не исполненную
# Теги
тип заявки определяется ее полем tags - Dict{tagname:{}}
используем tagname
если словарь tags содерхит ключ "Новое подключение" - это Заявка на подключение, если нет то на ремонт
если словарь tags содерхит ключ "Работы произведены" - Заявка Исполнена, иначе Не исполнена
## Для отметки исполнения заявки
надо изменить массив ее тегов Например
        "tags": {
            "Солнечногорск": {},
            "Новое подключение": {}
        }
Добавляем тег Работы произведены
и отправляем запрос на изменение заявки с json body
{
    "ticket": {
        "id": 32412,
        "tags": {
            "Солнечногорск": {},
            "Новое подключение": {},
            "Работы произведены": {}
        }
    }
}
Важно - элемент тег отправляется полностью а не только изменения
## Для для удаления отметки исполнения заявки
надо изменить массив ее тегов Например
        "tags": {
            "Солнечногорск": {},
            "Новое подключение": {},
            "Работы произведены": {}
        }
		Убираем тег Работы произведены
и отправляем запрос на изменение заявки с json body
{
    "ticket": {
        "id": 32412,
        "tags": {
            "Солнечногорск": {},
            "Новое подключение": {}
        }
    }
}
Важно - элемент тег отправляется полностью а не только изменения

# Контракт API
для вызова API Техпортала на backend используем переменные из .env
TP_BASE_TOKEN
TP_BASE_URL
## Список заявок
Получить список заявок пользователя на дату
method - POST
endpoint - tickets/get
пример  запроса на сегодня(29.07.2026 - в примере это текущий день, не имеет отношения к реальной дате когда ты читаешь этот файл)
{"page":0,"filters":{"and":[{"tags":{},"createdBy":[],"masterIds":[<id пользователя>],"closedFrom":"-","scheduledTo":"29.07.2026","scheduledFrom":"29.07.2026"}]}}
Пример ответа
[
    {
        "id": 49323,
        "createDate": "2026-07-28T12:03:19.822Z",
        "createdBy": 52,
        "clientId": 35509398,
        "phones": [
            "+79254553958"
        ],
        "masters": [
            87
        ],
        "scheduledDate": "2026-07-29T12:00:00.000Z",
        "hoursToFinish": "3.00",
        "closeDate": null,
        "description": "Новое подключение для Дмитровский р-н, д. Федоровка, СНТ Волга, уч. 96, подкл. 4000+1АП на счет, МО ЧС, 29 июля, 15-18, Андрей",
        "properties": {},
        "history": [
            {
                "ip": "217.76.32.101",
                "changes": [
                    {
                        "key": "scheduledDate",
                        "path": "$.scheduledDate",
                        "type": "UPDATE",
                        "value": "2026-07-29T12:00:00.000Z",
                        "oldValue": "2026-07-29T08:00:00.000Z",
                        "valueType": "Date"
                    },
                    {
                        "key": "hoursToFinish",
                        "path": "$.hoursToFinish",
                        "type": "UPDATE",
                        "value": 3,
                        "oldValue": "3.00",
                        "valueType": "Number"
                    }
                ],
                "createdAt": "2026-07-28T12:03:31.018Z",
                "techportalUser": 112
            },
            {
                "ip": "217.76.43.9",
                "changes": [
                    {
                        "key": "phones",
                        "path": "$.phones",
                        "type": "ADD",
                        "value": [
                            "+79254553958"
                        ],
                        "valueType": "Array"
                    },
                    {
                        "key": "description",
                        "path": "$.description",
                        "type": "ADD",
                        "value": "Новое подключение для Дмитровский р-н, д. Федоровка, СНТ Волга, уч. 96, подкл. 4000+1АП на счет, МО ЧС, 29 июля, 15-18, Андрей",
                        "valueType": "String"
                    },
                    {
                        "key": "address",
                        "path": "$.address",
                        "type": "ADD",
                        "value": {
                            "externalAddress": "Дмитровский р-н, д. Мишуково, СНТ Волга, д. 96"
                        },
                        "valueType": "Object"
                    },
                    {
                        "key": "hoursToFinish",
                        "path": "$.hoursToFinish",
                        "type": "ADD",
                        "value": 3,
                        "valueType": "Number"
                    },
                    {
                        "key": "scheduledDate",
                        "path": "$.scheduledDate",
                        "type": "ADD",
                        "value": "2026-07-29T08:00:00Z",
                        "valueType": "String"
                    },
                    {
                        "key": "masters",
                        "path": "$.masters",
                        "type": "ADD",
                        "value": [
                            87
                        ],
                        "valueType": "Array"
                    },
                    {
                        "key": "tags",
                        "path": "$.tags",
                        "type": "ADD",
                        "value": {
                            "Дмитров": null,
                            "Новое подключение": null
                        },
                        "valueType": "Object"
                    }
                ],
                "createdAt": "2026-07-28T12:03:19.822Z",
                "ticketCreated": true,
                "techportalUser": 52
            }
        ],
        "tags": {
            "Дмитров": {},
            "Новое подключение": {}
        },
        "clientPin": 35509398,
        "clientLogin": "35509398",
        "clientName": "Денис Денис",
        "address": {
            "id": 55289,
            "area": null,
            "city": null,
            "floor": null,
            "house": null,
            "region": null,
            "street": null,
            "country": null,
            "houseId": 85223,
            "section": null,
            "building": null,
            "district": null,
            "entrance": null,
            "apartment": "0",
            "externalHouse": "Дмитровский р-н, д. Мишуково, СНТ Волга, д. 96",
            "externalAddress": "Дмитровский р-н, д. Мишуково, СНТ Волга, д. 96"
        },
        "accessProvider": null,
        "clientPhone": "79254553958",
        "clientAdditionalContacts": "",
        "clientBranch": 2,
        "totalTickets": "5"
    },
	...
]
В карточке на странице списка выводим
address.externalAddress
clientPhone
В подробную информацию
clientName
description
ленту коментариев. Где искать комментарии
Заявка содержит список "history"
Пример
[            {
                "ip": "217.76.32.101",
                "changes": [
                    {
                        "key": "Работы произведены",
                        "path": "$.tags.Работы произведены",
                        "type": "ADD",
                        "value": null,
                        "valueType": null
                    },
                    {
                        "key": "МКД",
                        "path": "$.tags.МКД",
                        "type": "REMOVE",
                        "value": null,
                        "valueType": null
                    },
                    {
                        "key": "Новое подключение",
                        "path": "$.tags.Новое подключение",
                        "type": "REMOVE",
                        "value": null,
                        "valueType": null
                    }
                ],
                "createdAt": "2026-07-30T08:13:21.930Z",
                "techportalUser": 52
            },
            {
                "ip": "217.76.32.101",
                "changes": [
                    {
                        "key": "comments",
                        "path": "$.comments",
                        "type": "ADD",
                        "value": "https://s3.ccs.ru/minio-api/test/tp_32412_photo_2025-10-28_12-41-50.jpg",
                        "valueType": "String"
                    }
                ],
                "createdAt": "2025-12-08T09:27:01.906Z",
                "techportalUser": 3
            }
]
В каждом элементе списка есть список changes
элементы этого списка Dict
если элемент "key" = "comments" то это комемнтарий
ключ createdAt - дата коментария
ключ "techportalUser" - ользователь его оставивший
В ленту коментариев выводим элемент
Дата комментария, Имя пользователя, changes[x].value
данные пользователя можно получить из списка пользователей(описан в endpoint Получение списка пользователей)
## Получение списка пользователей
method - GET
endpoint - techportal-user/list
пример ответа
[
    {
        "id": 3,
        "phone": "79255807004",
        "email": "santor@ccs.ru",
        "properties": {
            "token": "<redacted>",
            "branch": 1,
            "number": "363",
            "tickets": {
                "customFilters": [
                    {
                        "name": "На сегодня",
                        "filters": {
                            "tags": {
                                "not": [
                                    {
                                        "tag": "Работы произведены"
                                    }
                                ]
                            },
                            "createdBy": [],
                            "masterIds": [],
                            "closedFrom": "-",
                            "scheduledTo": "сегодня",
                            "scheduledFrom": "сегодня"
                        }
                    },
                    {
                        "name": "подключки",
                        "filters": {
                            "tags": {
                                "and": [
                                    {
                                        "tag": "Новое подключение"
                                    },
                                    {
                                        "not": [
                                            {
                                                "or": [
                                                    {
                                                        "tag": "Подключение выполненно"
                                                    },
                                                    {
                                                        "tag": "Работы произведены"
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            },
                            "createdBy": [],
                            "masterIds": []
                        }
                    },
                    {
                        "name": "ремонт",
                        "filters": {
                            "tags": {
                                "and": [
                                    {
                                        "not": [
                                            {
                                                "or": [
                                                    {
                                                        "tag": "Работы произведены"
                                                    },
                                                    {
                                                        "tag": "Подключение выполненно"
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        "tag": "Заявка на выезд"
                                    }
                                ]
                            },
                            "createdBy": [],
                            "masterIds": []
                        }
                    },
                    {
                        "name": "ремонт 2",
                        "filters": {
                            "tags": {
                                "and": [
                                    {
                                        "not": [
                                            {
                                                "or": [
                                                    {
                                                        "tag": "Работы произведены"
                                                    },
                                                    {
                                                        "tag": "Подключение выполненно"
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        "or": [
                                            {
                                                "tag": "Заявка на выезд"
                                            },
                                            {
                                                "tag": "Новое подключение"
                                            }
                                        ]
                                    }
                                ]
                            },
                            "createdBy": [],
                            "masterIds": []
                        }
                    }
                ],
                "customSortingOrder": {
                    "ids": [
                        47096,
                        47095,
                        47094,
                        47093,
                        47085,
                        47084,
                        47078,
                        47076,
                        47067,
                        47066,
                        47065,
                        47064,
                        47061,
                        47060,
                        47059,
                        47058,
                        47057,
                        47052,
                        47047,
                        47046,
                        47044,
                        47042,
                        47041,
                        47040,
                        47039,
                        47038,
                        47037,
                        47036,
                        47035,
                        47034,
                        47033,
                        47031,
                        47026,
                        47023,
                        47016,
                        46970,
                        46917,
                        46883,
                        46879,
                        46844,
                        46795,
                        46670,
                        46307,
                        45258
                    ],
                    "createdAt": 1783620885247
                },
                "defaultCustomFilterId": 0
            },
            "permissions": {
                "map": {
                    "lines": "w",
                    "cameras": "w",
                    "clients": true,
                    "network": "w"
                },
                "tags": true,
                "client": {
                    "block": true,
                    "plans": "w",
                    "create": true,
                    "credit": "w",
                    "groups": "r",
                    "balance": "w",
                    "prepaid": "w",
                    "password": "w",
                    "onlineData": "w",
                    "additionalParams": "w"
                },
                "salary": true,
                "reports": {
                    "cashdeskOperations": true
                },
                "tickets": {
                    "all": true,
                    "create": true,
                    "brigades": true,
                    "extraClientDetails": true
                },
                "calendar": true,
                "messages": true,
                "schedule": "w",
                "switchPorts": true
            },
            "restoreLink": "<redacted>"
        },
        "status": "admin",
        "lastActive": null,
        "fbLogin": null,
        "ip": null,
        "firstName": "Константин",
        "middleName": "-",
        "lastName": "-",
        "name": "- Константин",
        "jobTitleId": null
    },
    {
        "id": 136,
        "phone": "79111111105",
        "email": "pwa@point.online",
        "properties": {
            "token": "<redacted>",
            "permissions": {
                "map": {
                    "lines": "r",
                    "cameras": "r",
                    "clients": true,
                    "network": "r",
                    "employeeLocations": true
                },
                "tags": true,
                "client": {
                    "block": true,
                    "plans": "w",
                    "create": true,
                    "credit": "w",
                    "groups": "w",
                    "balance": "w",
                    "prepaid": "w",
                    "password": "w",
                    "onlineData": "w",
                    "promisedPayment": true,
                    "additionalParams": "w"
                },
                "tickets": {
                    "all": true,
                    "create": true,
                    "brigades": true,
                    "accessProviders": "w",
                    "extraClientDetails": true
                },
                "messages": true,
                "schedule": "w",
                "agentsRun": true,
                "switchPorts": true
            }
        },
        "status": "admin",
        "lastActive": null,
        "fbLogin": null,
        "ip": null,
        "firstName": "pwa",
        "middleName": null,
        "lastName": "pwa",
        "name": "pwa pwa",
        "jobTitleId": null
    },
    {
        "id": 4,
        "phone": "79919503400",
        "email": "arhipov.energo@point.online",
        "properties": {
            "branch": 1,
            "number": "110",
            "tickets": {
                "customFilters": [],
                "customSortingOrder": {
                    "ids": [],
                    "createdAt": 1784537768402
                },
                "defaultCustomFilterId": null
            },
            "permissions": {
                "map": {
                    "lines": "w",
                    "cameras": "w",
                    "clients": true,
                    "network": "w",
                    "employeeLocations": true
                },
                "tags": true,
                "client": {
                    "block": true,
                    "plans": "w",
                    "create": true,
                    "credit": "w",
                    "groups": "w",
                    "balance": "w",
                    "prepaid": "w",
                    "password": "w",
                    "callerInfo": true,
                    "onlineData": "w",
                    "promisedPayment": true,
                    "additionalParams": "w"
                },
                "salary": true,
                "reports": {
                    "cashdeskOperations": true
                },
                "tickets": {
                    "all": true,
                    "files": "w",
                    "create": true,
                    "photos": "w",
                    "brigades": true,
                    "accessProviders": "r",
                    "extraClientDetails": true
                },
                "calendar": true,
                "messages": true,
                "schedule": "w",
                "switchPorts": true
            }
        },
        "status": "admin",
        "lastActive": null,
        "fbLogin": null,
        "ip": null,
        "firstName": "Андрей",
        "middleName": "",
        "lastName": "Архипов",
        "name": "Архипов Андрей",
        "jobTitleId": null
    },
    {
        "id": 1,
        "phone": "79037574349",
        "email": "sm@domolan.ru",
        "properties": {
            "branch": 1,
            "number": null,
            "tickets": {
                "customFilters": [
                    {
                        "name": "Актуально на сегодня",
                        "filters": {
                            "tags": {
                                "not": [
                                    {
                                        "or": [
                                            {
                                                "tag": "Подключение выполненно"
                                            },
                                            {
                                                "tag": "Работы произведены"
                                            },
                                            {
                                                "tag": "Перенесено нами"
                                            },
                                            {
                                                "tag": "Запланировано"
                                            },
                                            {
                                                "tag": "Отмена заявки"
                                            }
                                        ]
                                    }
                                ]
                            },
                            "createdBy": [],
                            "masterIds": [],
                            "closedFrom": "-",
                            "scheduledTo": "сегодня",
                            "scheduledFrom": "сегодня"
                        }
                    }
                ],
                "customSortingOrder": {
                    "ids": [],
                    "createdAt": 1765366965035
                },
                "defaultCustomFilterId": 0
            },
            "permissions": {
                "map": {
                    "lines": "w",
                    "cameras": "w",
                    "clients": true,
                    "network": "w",
                    "employeeLocations": true
                },
                "tags": true,
                "client": {
                    "block": true,
                    "plans": "w",
                    "create": true,
                    "credit": "w",
                    "groups": "w",
                    "balance": "w",
                    "prepaid": "w",
                    "password": "w",
                    "onlineData": "w",
                    "promisedPayment": true,
                    "additionalParams": "w"
                },
                "salary": true,
                "reports": {
                    "cashdeskOperations": true
                },
                "tickets": {
                    "all": true,
                    "files": "w",
                    "create": true,
                    "photos": "w",
                    "brigades": true,
                    "extraClientDetails": true
                },
                "calendar": true,
                "messages": true,
                "schedule": "w",
                "switchPorts": true,
                "conversationTags": "w"
            },
            "restoreLink": "<redacted>"
        },
        "status": "admin",
        "lastActive": null,
        "fbLogin": null,
        "ip": null,
        "firstName": "Сергей",
        "middleName": "Викторович",
        "lastName": "Мажугин",
        "name": "Мажугин Сергей",
        "jobTitleId": null
    }
]
## редактирование заявки
method - POST
endpoint - tickets/persist
пример вызова
{
    "ticket": {
        "id": 32412,
        "tags": {
            "Новое подключение": {},
            "Работы произведены": {}
        }
    }
}
В ответе сервера - json обновленой заявки
