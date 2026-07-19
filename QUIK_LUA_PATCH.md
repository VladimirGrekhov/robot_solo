# Патч QUIK lua: цвет текстовых меток (addLabel2)

Робот рисует цветные метки сделок на графике QUIK через кастомную lua-команду
`addLabel2` (в `qsfunctions.lua` моста QuikSharp/QuikPy). В стоковой версии этой
функции есть баг: цвет текстовых меток всегда получает полный красный канал.

## Симптом

BUY, отправленный как RGB `(0, 200, 0)` (зелёный), на графике выглядит **жёлтым**.
Любой цвет текстовой метки «прибит» полным красным — сделать зелёный/синий/циан
невозможно.

## Причина

`qsfunctions.lua`, функция `qsfunctions.addLabel2`, строка с дефолтами:

```lua
if text == "" then text = nil else r = 255 end
```

Ветка `else r = 255` означает: как только у метки есть текст, красный канал
принудительно ставится в 255, перекрывая переданное значение. Поэтому
`(0,200,0)` превращается в `(255,200,0)` — жёлтый.

Та же ошибка есть в `qsfunctions.setLabelParams` (эта команда роботом не
используется, но при желании чинится так же).

## Фикс

Нужны две правки в блоке «значения по умолчанию» функции `addLabel2`:

1. Убрать принудительный красный (`else r = 255`).
2. Перевести R/G/B (и прозрачности) в **числа** — `AddLabel` в QUIK ждёт цвет
   числами (ср. рабочую `addLabel`: `R = 255, G = 255, B = 255`). Если оставить
   строки из `split`, QUIK их не применяет и цвет не меняется.

Итоговый блок «значения по умолчанию»:

```lua
	if text == "" then text = nil end
	if imagePath == "" then imagePath = nil end
	if alignment == "" then alignment = nil end
	if hint == "" then hint = nil end
	if r == "-1" or r == "" then r = nil else r = tonumber(r) end
	if g == "-1" or g == "" then g = nil else g = tonumber(g) end
	if b == "-1" or b == "" then b = nil else b = tonumber(b) end
	if transparency == "-1" or transparency == "" then transparency = nil else transparency = tonumber(transparency) end
	if tranBackgrnd == "-1" or tranBackgrnd == "" then tranBackgrnd = nil else tranBackgrnd = tonumber(tranBackgrnd) end
	if fontName == "" then fontName = nil end
	if fontHeight == "-1" or fontHeight == "" then fontHeight = nil end
```

После этого `-1` = цвет по умолчанию QUIK, иначе — заданный числом цвет.

## Применение

1. Открыть `qsfunctions.lua` в папке скриптов QUIK (рядом с `qscallbacks.lua`,
   `qsutils.lua`, `config.json`).
2. Заменить блок «значения по умолчанию» в `addLabel2` на код выше.
3. Перезапустить QUIK, чтобы подхватилась новая версия lua (`require` кеширует
   модуль, поэтому простой стоп/старт скрипта версию не обновит — нужен рестарт
   терминала).

## Если цвет всё равно не меняется

- Проверить, что робот подключается к тому же серверу, чей `qsfunctions.lua`
  правился: в `config.json` может быть несколько серверов (QuikSharp, Quik_2) со
  своими папками скриптов.
- Удалить скомпилированный `qsfunctions.luac` в той же папке, если он есть — QUIK
  мог грузить его вместо `.lua`.
- Быстрый тест «правильный ли lua загружен»: временно прописать в `addLabel2`
  перед сборкой `labelParams` строки `r = 0 g = 0 b = 255` (числа). После рестарта
  QUIK все метки должны стать синими. Если стали — lua тот, правки работают;
  если нет — грузится другой файл.

## Связь с кодом робота

Python-сторона (`orb_robot.py`, `_add_label2`) шлёт поля строго в порядке,
который ждёт `addLabel2`:

```
chartTag | yValue | date | time | text | imagePath | alignment | hint | r | g | b | transparency | tranBackgrnd | fontName | fontHeight
```

Цвет — три отдельных поля `r|g|b` (позиции 9–11). После патча они попадают в
`labelParams.R/G/B` без искажений, и метки рисуются заданным цветом:
BUY — зелёный, SELL — красный, SL — оранжевый.
