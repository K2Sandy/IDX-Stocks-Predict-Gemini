import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from config import DATASET_DIR, DEBUG_DIR, SITUS, hari_ini_wib
from media_utils import deteksi_tanggal_dari_url, deteksi_tanggal_via_request

NAMA_FILE_AKHIR = os.path.join(DATASET_DIR, "url_selenium_kebijakan_ekonomi.csv")

# Berapa banyak request paralel untuk cek tanggal media yang tidak
# menaruh tanggal di URL-nya (Detik, Tempo). Jangan terlalu besar
# supaya tidak dianggap serangan oleh server media.
MAX_WORKERS_CEK_TANGGAL = 8


def _coba_tutup_banner_consent(driver):
    """Best-effort: beberapa situs memblokir render konten sampai banner
    cookie/consent ditutup. Aman kalau tidak ketemu (tidak melempar error)."""
    kandidat_teks = ["terima", "setuju", "accept", "oke", "ok", "izinkan"]
    try:
        tombol = driver.find_elements(By.TAG_NAME, "button")
        for t in tombol:
            teks = (t.text or "").strip().lower()
            if teks and any(k in teks for k in kandidat_teks) and len(teks) < 30:
                t.click()
                time.sleep(0.5)
                break
    except Exception:
        pass


def ambil_feed_terbaru(driver) -> dict:
    """Kembalikan dict {nama_situs: set(url)} -- dipisah per situs supaya
    bisa dilaporkan berapa banyak URL yang ditemukan per sumber."""
    hasil = {nama: set() for nama in SITUS}

    for nama, cfg in SITUS.items():
        print(f"\n[{nama.upper()}] Menyisir indeks berita terbaru...")
        for i, url_indeks in enumerate(cfg["urls"], start=1):
            try:
                driver.get(url_indeks)
                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.TAG_NAME, "a"))
                    )
                except Exception:
                    pass  # kalau timeout, tetap lanjut baca apa yang sudah termuat
                _coba_tutup_banner_consent(driver)
                time.sleep(2)  # beri waktu tambahan untuk elemen yang di-load via JS
                soup = BeautifulSoup(driver.page_source, "html.parser")
                baru = 0
                for a in soup.find_all("a", href=True):
                    href = urljoin(driver.current_url, a["href"])
                    if cfg["cocok"](href) and href not in hasil[nama]:
                        hasil[nama].add(href)
                        baru += 1
                label_halaman = f" (halaman {i}/{len(cfg['urls'])})" if len(cfg["urls"]) > 1 else ""
                print(f"  -> {baru} URL baru dari{label_halaman} {url_indeks}")

                if baru == 0 and i == 1:
                    # Kalau HALAMAN PERTAMA sekalipun 0 hasil, kemungkinan
                    # besar selector "cocok" sudah basi atau situs butuh
                    # render JS ekstra -- simpan HTML mentahnya buat dicek.
                    os.makedirs(DEBUG_DIR, exist_ok=True)
                    debug_path = os.path.join(DEBUG_DIR, f"{nama}.html")
                    with open(debug_path, "w", encoding="utf-8") as f:
                        f.write(driver.page_source)
                    print(f"     [DEBUG] URL saat ini : {driver.current_url}")
                    print(f"     [DEBUG] Judul halaman: {driver.title!r}")
                    print(f"     [DEBUG] HTML disimpan ke: {debug_path} (buka & cek isinya)")
            except Exception as e:
                print(f"  -> Error {nama} ({url_indeks}): {e}")
        print(f"  => Total {nama}: {len(hasil[nama])} URL mentah.")

    return hasil


def filter_khusus_hari_ini(url_per_situs: dict) -> list:
    """
    Dari SEMUA url mentah yang ditemukan Selenium, hanya loloskan yang
    tanggal terbitnya = hari ini (WIB). Dilakukan di sini (tahap pencarian
    URL), bukan menunggu extract_berita.py mendownload seluruh isi
    artikel satu-satu baru ketahuan tanggalnya.
    """
    hari_ini = hari_ini_wib()
    url_lolos = []
    url_perlu_cek = []

    # Tahap 1: cek gratis lewat pola URL (Kompas/CNBC/CNN taruh tanggal di slug)
    for nama, urls in url_per_situs.items():
        for url in urls:
            tgl = deteksi_tanggal_dari_url(url)
            if tgl is None:
                url_perlu_cek.append(url)
            elif tgl == hari_ini:
                url_lolos.append(url)
            # kalau tgl ketemu tapi bukan hari ini -> otomatis dibuang, tanpa request

    print(
        f"\n[FILTER] {len(url_lolos)} URL lolos langsung dari pola URL, "
        f"{len(url_perlu_cek)} URL perlu dicek manual (Detik/Tempo/dll)."
    )

    # Tahap 2: untuk yang tidak punya tanggal di URL, cek via request ringan,
    # dijalankan paralel supaya tidak menunggu satu-satu.
    if url_perlu_cek:
        session = requests.Session()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_CEK_TANGGAL) as executor:
            future_ke_url = {
                executor.submit(deteksi_tanggal_via_request, url, session): url
                for url in url_perlu_cek
            }
            selesai = 0
            for future in as_completed(future_ke_url):
                url = future_ke_url[future]
                selesai += 1
                if selesai % 20 == 0:
                    print(f"  -> cek tanggal: {selesai}/{len(url_perlu_cek)}")
                try:
                    tgl = future.result()
                except Exception:
                    tgl = None
                if tgl == hari_ini:
                    url_lolos.append(url)

    return url_lolos


def jalankan_pencarian_harian():
    if not os.path.exists(DATASET_DIR):
        os.makedirs(DATASET_DIR)

    chrome_options = Options()
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    # Headless otomatis kalau jalan di CI (GitHub Actions selalu set CI=true),
    # atau kalau kamu sengaja set HEADLESS=true di mesin lokal. Kalau dites
    # manual di laptop tanpa variabel ini, browser tetap kebuka kelihatan
    # supaya gampang di-debug.
    jalan_headless = os.environ.get("CI") or os.environ.get("HEADLESS", "").lower() == "true"
    if jalan_headless:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")

    print("[INFO] Membuka browser otomatis" + (" (headless)..." if jalan_headless else " tunggal..."))
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=chrome_options
    )

    try:
        url_per_situs = ambil_feed_terbaru(driver)
    finally:
        driver.quit()

    total_mentah = sum(len(v) for v in url_per_situs.values())
    if total_mentah == 0:
        print("\n[WARNING] Tidak ada URL yang berhasil ditemukan sama sekali.")
        return

    url_hari_ini = filter_khusus_hari_ini(url_per_situs)

    if url_hari_ini:
        df_link = pd.DataFrame(sorted(set(url_hari_ini)), columns=["URL_Target"])
        df_link.to_csv(NAMA_FILE_AKHIR, index=False)
        print(
            f"\n[SUKSES] {len(df_link)} URL berita KHUSUS HARI INI "
            f"(dari {total_mentah} URL mentah) disimpan ke {NAMA_FILE_AKHIR}."
        )
    else:
        print(
            "\n[WARNING] Tidak ada URL yang tanggalnya cocok dengan hari ini. "
            "Kemungkinan situs belum update index, atau selector perlu dicek ulang."
        )


if __name__ == "__main__":
    jalankan_pencarian_harian()
