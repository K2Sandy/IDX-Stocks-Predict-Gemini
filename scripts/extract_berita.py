import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

from config import DATASET_DIR, hari_ini_wib
from media_utils import ekstrak_detail_artikel

NAMA_FILE_CSV = os.path.join(DATASET_DIR, "url_selenium_kebijakan_ekonomi.csv")
FILE_DATASET_BERITA = os.path.join(DATASET_DIR, "dataset_berita.csv")

# ==========================================
# DIKUNCI KHUSUS HARI INI
# ==========================================
# Sejak cari_url.py sekarang sudah memfilter tanggal di tahap pencarian URL,
# pengecekan ini di sini hanya jadi jaring pengaman kedua (misal ada artikel
# yang jam terbitnya berubah setelah dicek pertama kali). Harusnya jarang
# menolak apa pun karena input sudah bersih.
MODE_HARIAN_KETAT = True
# ==========================================

MAX_WORKERS_EKSTRAKSI = 6


def jalankan_ekstraksi_harian():
    if not os.path.exists(NAMA_FILE_CSV):
        print(f"[ERROR] Berkas {NAMA_FILE_CSV} tidak ditemukan. Jalankan cari_url.py dulu.")
        return

    print(f"Membaca daftar URL dari {NAMA_FILE_CSV}...")
    df_sumber = pd.read_csv(NAMA_FILE_CSV)
    daftar_url = df_sumber["URL_Target"].dropna().tolist()
    total = len(daftar_url)
    print(f"Memulai ekstraksi konten untuk {total} artikel (paralel, {MAX_WORKERS_EKSTRAKSI} worker)...\n")

    hari_ini = hari_ini_wib()
    data_berita = []
    jumlah_gagal = 0
    jumlah_dilewati_tanggal = 0

    session = requests.Session()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_EKSTRAKSI) as executor:
        future_ke_url = {
            executor.submit(ekstrak_detail_artikel, url, session): url for url in daftar_url
        }
        selesai = 0
        for future in as_completed(future_ke_url):
            selesai += 1
            hasil = future.result()

            if hasil["status"] == "sukses":
                if MODE_HARIAN_KETAT and hasil["Tanggal"] != hari_ini:
                    jumlah_dilewati_tanggal += 1
                    continue
                data_berita.append(
                    {
                        "Judul": hasil["Judul"],
                        "Tanggal": hasil["Tanggal"],
                        "Isi Berita": hasil["Isi Berita"],
                        "Sektor": hasil["Sektor"],
                        "URL": hasil["URL"],
                    }
                )
                print(f"[{selesai}/{total}] Sukses: {hasil['Judul'][:40]}... [{hasil['Sektor']}]")
            else:
                jumlah_gagal += 1

    if data_berita:
        df_baru = pd.DataFrame(data_berita)
        df_baru.to_csv(FILE_DATASET_BERITA, index=False, encoding="utf-8")
        print(
            f"\n[SUKSES] Berita hari ini dikunci. Total: {len(df_baru)} artikel siap dianalisis Gemini."
        )
    else:
        print("\n[WARNING] Tidak ada konten berita yang berhasil diekstrak.")

    print(
        f"[RINGKASAN] sukses={len(data_berita)}, gagal_konten/error={jumlah_gagal}, "
        f"dilewati_tanggal={jumlah_dilewati_tanggal}"
    )


if __name__ == "__main__":
    jalankan_ekstraksi_harian()
