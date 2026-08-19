"""
media_utils.py
================
Modul bersama untuk seluruh pipeline scraping berita.

Kenapa modul ini dibuat terpisah:
- Sebelumnya, logic "cara membaca judul/tanggal/isi per media" hanya ada di
  extract_berita.py. Akibatnya cari_url.py tidak bisa tahu tanggal artikel
  tanpa mendownload seluruh halaman lagi nanti. Dengan menaruh logic ini di
  satu tempat, cari_url.py bisa memfilter "khusus hari ini" SEJAK TAHAP
  PENCARIAN URL, bukan belakangan setelah semua diunduh penuh.

Isi modul:
- deteksi_tanggal_dari_url(): cek cepat TANPA request sama sekali, untuk
  media yang menaruh tanggal terbit langsung di slug URL-nya
  (Kompas, CNBC Indonesia, CNN Indonesia). Kalau pola tidak cocok,
  fungsi ini mengembalikan None -> caller harus fallback ke request asli.
- ekstrak_detail_artikel(): request + parsing penuh (judul, tanggal, isi)
  untuk satu URL. Dipakai baik oleh cari_url.py (mode ringan, cukup untuk
  cek tanggal) maupun extract_berita.py (mode penuh, ambil semua field).
- deteksi_sektor_sederhana(): menebak sektor artikel berdasarkan kata
  kunci di config.SEKTOR -- edit daftar sektor/kata kunci di config.py,
  bukan di sini.

CATATAN PENTING:
Selector HTML (class/tag) di bawah ini mengikuti struktur situs per Agustus
2026 versi kode aslimu. Situs berita sering ganti struktur HTML tanpa
pemberitahuan, jadi kalau suatu saat ekstraksi tiba-tiba banyak gagal,
langkah pertama adalah cek ulang selector-nya (buka salah satu artikel,
Inspect Element, cocokkan class-nya).

NAMBAH SITUS BARU: tambah fungsi _parse_<nama> baru di bawah (contoh
seperti _parse_tempo dkk), lalu daftarkan di _PARSER_PER_DOMAIN. Jangan
lupa juga tambah entry-nya di config.SITUS.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup, SoupStrainer

from config import SEKTOR as _KATA_KUNCI_SEKTOR

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

REQUEST_TIMEOUT = 20  # detik


# ==========================================================
# 1. DETEKSI TANGGAL LANGSUNG DARI URL (tanpa request/download)
# ==========================================================
_POLA_TANGGAL_URL = [
    # Kompas: .../read/2026/08/18/070000226/judul-berita
    re.compile(r"kompas\.com/read/(\d{4})/(\d{2})/(\d{2})/"),
    # CNBC Indonesia & CNN Indonesia: .../20260818070000-4-123456/judul
    re.compile(r"(?:cnbcindonesia|cnnindonesia)\.com/[a-z-]+/(\d{4})(\d{2})(\d{2})\d{6}-"),
]


def deteksi_tanggal_dari_url(url: str) -> Optional[str]:
    """Kembalikan 'YYYY-MM-DD' jika slug URL mengandung tanggal, else None."""
    for pola in _POLA_TANGGAL_URL:
        m = pola.search(url)
        if m:
            tahun, bulan, tanggal = m.groups()
            try:
                return datetime(int(tahun), int(bulan), int(tanggal)).strftime("%Y-%m-%d")
            except ValueError:
                return None
    return None


# --------------------------------------------------------------------------
# emitennews.com dan snips.stockbit.com TIDAK menaruh tanggal terbit di meta
# tag <head> sama sekali (beda dari 5 situs lain) -- tanggalnya cuma ada
# sebagai teks biasa di body halaman. Dua fungsi ini dipakai bareng-bareng
# oleh deteksi_tanggal_via_request() (cek cepat di cari_url.py) DAN
# _parse_emitennews/_parse_snips() (ekstraksi penuh di extract_berita.py),
# supaya pola regexnya cuma didefinisikan sekali.
# --------------------------------------------------------------------------

def _cari_tanggal_emitennews(teks: str) -> Optional[str]:
    # Formatnya "19/08/2026, 09:33 WIB" -- muncul pertama kali persis di
    # bawah nama penulis, sebelum isi artikel.
    m = re.search(r"(\d{2})/(\d{2})/(\d{4}),\s*\d{2}:\d{2}\s*WIB", teks)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _cari_tanggal_snips(teks: str) -> Optional[str]:
    # Formatnya "August 18, 2026" -- muncul pertama kali persis di bawah judul.
    m = re.search(
        r"(January|February|March|April|May|June|July|August|September"
        r"|October|November|December)\s+\d{1,2},\s+\d{4}",
        teks,
    )
    if not m:
        return None
    try:
        return datetime.strptime(m.group(0), "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def deteksi_tanggal_via_request(url: str, session: Optional[requests.Session] = None) -> Optional[str]:
    """
    Fallback untuk media yang URL-nya TIDAK mengandung tanggal (Detik, Tempo,
    dan sekarang juga Emitennews, Snips). Dipakai khusus di tahap PENCARIAN
    URL (cari_url.py), bukan tahap ekstraksi.
    """
    getter = session.get if session else requests.get
    try:
        res = getter(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        res.encoding = "utf-8"

        # Emitennews & Snips: tanggalnya cuma ada di teks body, bukan meta
        # tag -- jadi keduanya butuh parsing HALAMAN PENUH, bukan cuma
        # <head> seperti situs lain (lebih berat, tapi cuma untuk 2 situs ini).
        if "emitennews.com" in url:
            soup = BeautifulSoup(res.text, "html.parser")
            return _cari_tanggal_emitennews(soup.get_text(separator="\n"))
        if "snips.stockbit.com" in url:
            soup = BeautifulSoup(res.text, "html.parser")
            return _cari_tanggal_snips(soup.get_text(separator="\n"))

        soup = BeautifulSoup(res.text, "html.parser", parse_only=SoupStrainer("head"))
        meta_date = (
            soup.find("meta", itemprop="datePublished")
            or soup.find("meta", property="article:published_time")
            or soup.find("meta", attrs={"name": "publishdate"})
        )
        if meta_date and meta_date.get("content"):
            return meta_date["content"][:10]
    except requests.exceptions.RequestException:
        pass
    return None


# ==========================================================
# 2. DETEKSI SEKTOR (kata kunci sekarang datang dari config.SEKTOR)
# ==========================================================

def deteksi_sektor_sederhana(judul: str, isi: str) -> str:
    teks = f" {str(judul).lower()} {str(isi).lower()} "
    sektor_list = [
        sektor
        for sektor, kata_kunci in _KATA_KUNCI_SEKTOR.items()
        if kata_kunci and any(k in teks for k in kata_kunci)
    ]
    return ", ".join(sektor_list) if sektor_list else "Umum/Makro"


# ==========================================================
# 3. EKSTRAKSI KONTEN PER MEDIA (request + parsing)
# ==========================================================

def _parse_tempo(soup: BeautifulSoup):
    judul_el = soup.find("h1")
    judul = judul_el.text.strip() if judul_el else ""
    meta_date = soup.find("meta", itemprop="datePublished") or soup.find(
        "meta", property="article:published_time"
    )
    tanggal = meta_date["content"][:10] if meta_date and meta_date.get("content") else ""
    wadah = soup.find("article") or soup.find("main") or soup.find("div", class_="detail-konten")
    p_tags = wadah.find_all("p") if wadah else soup.find_all("p")
    isi = " ".join(p.text.strip() for p in p_tags if len(p.text.strip()) > 20)
    return judul, tanggal, isi


def _parse_cnbc(soup: BeautifulSoup):
    judul_el = soup.find("h1")
    judul = judul_el.text.strip() if judul_el else ""
    meta_date = soup.find("meta", property="article:published_time") or soup.find(
        "meta", attrs={"name": "publishdate"}
    )
    tanggal = meta_date["content"][:10] if meta_date and meta_date.get("content") else ""
    detil = soup.find("div", class_="detail_text")
    isi = ""
    if detil:
        p_tags = detil.find_all("p")
        isi = " ".join(p.text.strip() for p in p_tags if not p.find("strong"))
    return judul, tanggal, isi


def _parse_cnn(soup: BeautifulSoup):
    judul_el = soup.find("h1", class_="title") or soup.find("h1")
    judul = judul_el.text.strip() if judul_el else ""
    meta_date = soup.find("meta", property="article:published_time")
    tanggal = meta_date["content"][:10] if meta_date and meta_date.get("content") else ""
    detil = soup.find("div", class_="detail-text") or soup.find("div", class_="detail_text")
    isi = " ".join(p.text.strip() for p in detil.find_all("p")) if detil else ""
    return judul, tanggal, isi


def _parse_detik(soup: BeautifulSoup):
    judul_el = soup.find("h1")
    judul = judul_el.text.strip() if judul_el else ""
    meta_date = soup.find("meta", property="article:published_time") or soup.find(
        "meta", attrs={"name": "publishdate"}
    )
    tanggal = meta_date["content"][:10] if meta_date and meta_date.get("content") else ""
    detil = soup.find("div", class_="detail__body-text") or soup.find("div", id="detikdetailtext")
    isi = " ".join(p.text.strip() for p in detil.find_all("p")) if detil else ""
    return judul, tanggal, isi


def _parse_kompas(soup: BeautifulSoup):
    judul_el = soup.find("h1", class_="read__title")
    judul = judul_el.text.strip() if judul_el else ""
    meta_date = soup.find("meta", property="article:published_time")
    tanggal = meta_date["content"][:10] if meta_date and meta_date.get("content") else ""
    detil = soup.find("div", class_="read__content")
    isi = " ".join(p.text.strip() for p in detil.find_all("p")) if detil else ""
    return judul, tanggal, isi


def _parse_emitennews(soup: BeautifulSoup):
    judul_el = soup.find("h1")
    judul = judul_el.text.strip() if judul_el else ""

    teks_penuh = soup.get_text(separator="\n")
    tanggal = _cari_tanggal_emitennews(teks_penuh) or ""

    # Situs ini tidak punya nama class artikel yang bisa dipastikan dari
    # sini (lihat catatan di kepala file) -- ambil semua <p> yang cukup
    # panjang sebagai isi. Paragraf UI/nav biasanya pendek, jadi ambang
    # 60 karakter cukup buat menyaringnya.
    kandidat = [p.text.strip() for p in soup.find_all("p")]
    isi = " ".join(t for t in kandidat if len(t) > 60)
    return judul, tanggal, isi


def _parse_snips(soup: BeautifulSoup):
    judul_el = soup.find("h1")
    judul = judul_el.text.strip() if judul_el else ""

    teks_penuh = soup.get_text(separator="\n")
    tanggal = _cari_tanggal_snips(teks_penuh) or ""

    # Snips formatnya kaya (tabel performa harian, bullet list per saham,
    # beberapa sub-judul) -- BUKAN cuma <p> biasa seperti situs lain, jadi
    # ambil dari teks mentah antara tanggal dan penanda "Tags:"/"Disclaimer:"
    # di akhir, bukan cuma tag <p> (yang akan melewatkan isi tabel/list).
    m_tanggal = re.search(
        r"(January|February|March|April|May|June|July|August|September"
        r"|October|November|December)\s+\d{1,2},\s+\d{4}",
        teks_penuh,
    )
    awal = teks_penuh.find(m_tanggal.group(0)) + len(m_tanggal.group(0)) if m_tanggal else 0
    akhir = teks_penuh.find("Tags:")
    if akhir == -1:
        akhir = teks_penuh.find("Disclaimer:")
    potongan = teks_penuh[awal:akhir] if akhir != -1 else teks_penuh[awal:awal + 6000]
    isi = re.sub(r"\n{2,}", "\n", potongan).strip()
    return judul, tanggal, isi


def _parse_indopremier(soup: BeautifulSoup):
    judul_el = soup.find("h1")
    judul = judul_el.text.strip() if judul_el else ""

    # Situs ini beruntung punya meta tag standar, sama seperti CNBC/CNN/dll.
    meta_date = soup.find("meta", property="article:published_time")
    tanggal = meta_date["content"][:10] if meta_date and meta_date.get("content") else ""

    teks_penuh = soup.get_text(separator="\n")
    m_wib = re.search(r"\b\d{1,2}:\d{2}\s*WIB\b", teks_penuh)
    awal = teks_penuh.find(m_wib.group(0)) + len(m_wib.group(0)) if m_wib else 0
    akhir = teks_penuh.find("Sumber :")
    if akhir == -1:
        akhir = teks_penuh.find("BUKA AKUN")
    potongan = teks_penuh[awal:akhir] if akhir != -1 else teks_penuh[awal:awal + 4000]
    isi = re.sub(r"\n{2,}", "\n", potongan).strip()
    return judul, tanggal, isi


_PARSER_PER_DOMAIN = [
    ("tempo.co", _parse_tempo),
    ("cnbcindonesia.com", _parse_cnbc),
    ("cnnindonesia.com", _parse_cnn),
    ("detik.com", _parse_detik),
    ("kompas.com", _parse_kompas),
    ("emitennews.com", _parse_emitennews),
    ("snips.stockbit.com", _parse_snips),
    ("indopremier.com", _parse_indopremier),
]


def _pilih_parser(url: str):
    for domain, fn in _PARSER_PER_DOMAIN:
        if domain in url:
            return fn
    return None


def ekstrak_detail_artikel(url: str, session: Optional[requests.Session] = None) -> dict:
    """
    Request + parse satu artikel. Selalu mengembalikan dict dengan key 'status':
      - 'sukses'        -> beserta Judul, Tanggal, Isi Berita, Sektor, URL
      - 'gagal_konten'  -> halaman kebuka tapi judul/isi tidak ketemu
      - 'error'         -> exception jaringan/HTTP, beserta 'pesan'
    Tidak melempar exception ke caller -> aman dipakai di ThreadPoolExecutor.
    """
    parser = _pilih_parser(url)
    if parser is None:
        return {"status": "error", "pesan": "domain tidak dikenali", "url": url}

    getter = session.get if session else requests.get
    try:
        res = getter(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        res.encoding = "utf-8"
        soup = BeautifulSoup(res.text, "html.parser")

        judul, tgl_mentah, isi = parser(soup)
        if not judul or len(isi) < 100:
            return {"status": "gagal_konten", "url": url}

        tanggal_final = tgl_mentah if tgl_mentah else datetime.now().strftime("%Y-%m-%d")
        sektor = deteksi_sektor_sederhana(judul, isi)

        return {
            "status": "sukses",
            "Judul": judul,
            "Tanggal": tanggal_final,
            "Isi Berita": isi,
            "Sektor": sektor,
            "URL": url,
        }
    except requests.exceptions.RequestException as e:
        return {"status": "error", "pesan": str(e), "url": url}
    except Exception as e:  # noqa: BLE001 - kita memang mau menelan semua error parsing
        return {"status": "error", "pesan": f"parsing gagal: {e}", "url": url}
