"""Как читать файл локали, который написал не ты.

КОПИЯ правил из `backend/translator.py` (`decode_upload`), намеренно построчная: `cli`
ставится у клиента отдельным пакетом и бэкенд не импортирует. Расхождение двух копий
стережёт `cli/tests/test_encoding_parity.py` — тот же корпус байтов обеим сторонам.

Зачем вообще: `open(path, encoding="utf-8")` — это отказ читать половину настоящих файлов.
`kaeris check` на обычном JSON с BOM (его ставит Visual Studio и «Блокнот») выходил с кодом
2 и печатал «Unexpected UTF-8 BOM (decode using utf-8-sig)» — питоновский жаргон в чужом CI,
и проверка не выполнялась вовсе. Xcode пишет `.strings` и каталоги в UTF-16.
"""
import codecs

_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"),
)


class UnknownEncoding(ValueError):
    """Байты не читаются ни в одной кодировке, которую мы беремся определить.

    Гадать вместо отказа нельзя: неверно угаданная однобайтовая кодировка даёт не ромбики,
    а ДРУГИЕ буквы — правдоподобно выглядящий неправильный текст."""


def _bomless_wide_encoding(raw):
    """UTF-16/32 БЕЗ BOM, опознанные по расположению нулевых байтов, иначе None.

    Проверяется ДО UTF-8 намеренно: у ASCII-документа в UTF-16 каждый второй байт нулевой,
    а нулевой байт — совершенно законный UTF-8, поэтому строгий utf-8 такой файл принимает
    и отдаёт текст с дырами между буквами."""
    head = raw[:4096]
    if b"\x00" not in head:
        return None
    if len(head) >= 4 and head[:4].count(0) == 3:
        return "utf-32-le" if head[0] else "utf-32-be"
    чётные = head[0::2].count(0)
    нечётные = head[1::2].count(0)
    порог = max(2, len(head) // 8)
    if нечётные >= порог and чётные == 0:
        return "utf-16-le"
    if чётные >= порог and нечётные == 0:
        return "utf-16-be"
    return None


def decode_bytes(content, filename=""):
    """Байты файла → текст. Отказ вместо порчи, если кодировка не опознана."""
    for bom, enc in _BOMS:
        if content.startswith(bom):
            try:
                return content.decode(enc)      # BOM съедается кодеком, а не едет в текст
            except UnicodeDecodeError as e:
                raise UnknownEncoding(
                    f"the file starts with a {enc} byte-order mark but is not valid {enc} "
                    f"(byte 0x{content[e.start]:02X} at offset {e.start})") from None

    wide = _bomless_wide_encoding(content)
    if wide:
        try:
            return content.decode(wide)
        except UnicodeDecodeError:
            pass                                # не широкая кодировка — пробуем дальше

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        # .properties по спецификации Java — ISO-8859-1, и Java 9+ читает его ровно так:
        # сперва UTF-8, при неудаче latin-1. Единственная кодировка, которую мы не гадаем,
        # а знаем из формата, поэтому единственная разрешённая запасная.
        if filename.lower().endswith(".properties"):
            return content.decode("latin-1")
        # Текст сообщения — слово в слово как в бэкенде: советы («сохраните как UTF-8»)
        # добавляет вызывающий, иначе две копии разъезжаются на первой же правке.
        raise UnknownEncoding(
            f"byte 0x{content[e.start]:02X} at offset {e.start} is not valid UTF-8") from None

    if "\x00" in text:
        raise UnknownEncoding("the file contains NUL bytes, so it is not text in any encoding "
                              "we can read")
    return text


def read_text(path):
    """Файл на диске → текст по тем же правилам."""
    with open(path, "rb") as f:
        return decode_bytes(f.read(), path)
