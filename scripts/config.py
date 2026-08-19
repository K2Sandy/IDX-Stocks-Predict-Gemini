"""
config.py
=============================================================
SATU TEMPAT untuk semua hal yang mau kamu ubah atau tambah nanti:
  - Sektor yang dianalisis (SEKTOR)
  - Situs berita yang di-scrape (SITUS)
  - Path folder dataset/docs

Skrip lain (cari_url.py, media_utils.py, extract_berita.py,
analisis_gemini.py) semua membaca dari file ini. Nambah sektor atau
situs baru = edit di sini saja, tidak perlu bongkar file lain.

CATATAN kalau nambah situs berita baru:
  1. Tambah entry baru di SITUS di bawah (url index + fungsi "cocok").
  2. Tambah fungsi parser-nya di media_utils.py (cari nama fungsi
     _parse_<situs_lain> di sana buat dicontoh, lalu daftarkan di
     _PARSER_PER_DOMAIN).
Nambah sektor baru CUKUP edit SEKTOR di bawah -- tidak perlu ubah file
lain sama sekali, baik untuk deteksi sektor artikel, urutan analisis
Gemini, maupun tampilan website (semua otomatis ikut).
"""
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------
# WAKTU -- selalu pakai WIB, apapun timezone mesin yang menjalankan.
# Ini penting karena GitHub Actions jalan di UTC secara default; tanpa ini
# "hari ini" bisa salah tanggal kalau dijalankan dekat tengah malam WIB.
# --------------------------------------------------------------------------
ZONA_WIB = ZoneInfo("Asia/Jakarta")


def sekarang_wib():
    return datetime.now(ZONA_WIB)


def hari_ini_wib():
    return sekarang_wib().strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# PATH -- dihitung dari lokasi file ini, jadi selalu benar mau dijalankan
# dari folder mana pun (lokal maupun di GitHub Actions).
# --------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(BASE_DIR, "..", "dataset")
DEBUG_DIR = os.path.join(BASE_DIR, "..", "debug_output")
DOCS_DIR = os.path.join(BASE_DIR, "..", "docs")
DOCS_DATA_DIR = os.path.join(DOCS_DIR, "data")

# --------------------------------------------------------------------------
# SEKTOR -- tambah baris baru di sini untuk menambah sektor baru.
# Kata kunci dipakai media_utils.py buat menebak sektor sebuah artikel.
# "Umum/Makro" sengaja dikosongkan -- itu fallback otomatis kalau tidak
# ada kata kunci sektor lain yang cocok, bukan sektor yang "dicari".
# --------------------------------------------------------------------------
SEKTOR = {
    "Keuangan": ["saham", "bank", "bunga", "keuangan", "finansial", "ojk", "bi rate", " bi "],
    "Teknologi": ["teknologi", "startup", " ai ", "aplikasi", "digital", "chip", "gadget"],
    "Energi": ["energi", "minyak", "batubara", "pln", "gas", "bensin"],
    "Infrastruktur": ["infrastruktur", "tol", "jembatan", "konstruksi", "bumn", "komunikasi"],
    "Umum/Makro": [],
}

# Urutan sektor yang dianalisis satu-satu oleh Gemini (analisis_gemini.py)
# -- otomatis ikut SEKTOR di atas, tidak perlu diketik ulang.
SEKTOR_TARGET = list(SEKTOR.keys())

# --------------------------------------------------------------------------
# SITUS BERITA -- tambah entry baru di sini untuk menambah sumber berita.
# "cocok" = fungsi yang menentukan apakah sebuah URL adalah artikel berita
# beneran (bukan halaman iklan/index/video/dll).
# --------------------------------------------------------------------------
_TEMPO_KANAL_DIKECUALIKAN = {"subscribe", "sales", "info-tempo", "indeks", "tag", "cekfakta", "newsletter"}

SITUS = {
    "tempo": {
        "url": "https://www.tempo.co/indeks",
        "cocok": lambda href: bool(
            (m := re.search(r"tempo\.co/([a-z-]+)/[a-z0-9-]+-\d{5,8}/?$", href))
        )
        and m.group(1) not in _TEMPO_KANAL_DIKECUALIKAN,
    },
    "cnbc": {
        "url": "https://www.cnbcindonesia.com/indeks",
        "cocok": lambda href: bool(re.search(r"cnbcindonesia\.com/[a-z]+/\d{14}-\d+-\d+", href)),
    },
    "cnn": {
        "url": "https://www.cnnindonesia.com/indeks",
        "cocok": lambda href: "cnnindonesia.com" in href and len(href.split("/")) > 4
        and not any(x in href for x in ["televisi", "video"]),
    },
    "detik": {
        "url": "https://news.detik.com/indeks",
        "cocok": lambda href: "news.detik.com/berita" in href and "/d-" in href,
    },
    "kompas": {
        "url": "https://indeks.kompas.com/",
        "cocok": lambda href: "kompas.com/read/" in href,
    },
}
