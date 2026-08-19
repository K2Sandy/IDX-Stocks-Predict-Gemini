import json
import os
import re
import time

import pandas as pd
from google import genai

from config import DATASET_DIR, DOCS_DATA_DIR, SEKTOR_TARGET, hari_ini_wib, sekarang_wib

FILE_DATASET_BERITA = os.path.join(DATASET_DIR, "dataset_berita.csv")

# Key sekarang dibaca dari environment variable, BUKAN ditulis langsung di
# sini -- supaya aman dipush ke GitHub (repo publik akan langsung membocorkan
# key kalau ditulis literal di kode). Set lewat:
#   - lokal: export GEMINI_API_KEY=xxxx   (atau set di file .env kamu sendiri)
#   - GitHub Actions: Settings > Secrets and variables > Actions > New repository
#     secret, nama GEMINI_API_KEY, lalu workflow membacanya via ${{ secrets.GEMINI_API_KEY }}
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NAMA_MODEL = "gemini-2.5-flash"
MAX_PERCOBAAN = 3
# ==========================================


def panggil_gemini_dengan_retry(client, prompt: str) -> str:
    """Retry sederhana untuk error sementara (rate limit / timeout)."""
    for percobaan in range(1, MAX_PERCOBAAN + 1):
        try:
            response = client.models.generate_content(model=NAMA_MODEL, contents=prompt)
            return response.text.strip()
        except Exception as e:
            if percobaan == MAX_PERCOBAAN:
                raise
            tunggu = 2 * percobaan
            print(f"  -> Percobaan {percobaan} gagal ({e}), coba lagi dalam {tunggu}s...")
            time.sleep(tunggu)


# ==========================================================
# PARSING FIELD DARI RESPONS GEMINI
# ==========================================================
# Gemini diminta menjawab dengan format field tetap (SEKTOR:, PREDIKSI:, dst).
# Fungsi-fungsi ini menarik nilai tiap field dari teks itu -- dipakai untuk
# (a) ringkasan "saham spesifik" di paling atas output konsol, dan
# (b) file JSON terstruktur yang dibaca website (lihat simpan_hasil_json).

def _ekstrak_field(teks, nama_field):
    """Ambil isi satu baris 'NAMA_FIELD: isinya', toleran kalau Gemini
    menambahkan markdown bold (**) di sekitar nama field atau isinya."""
    m = re.search(rf"\*{{0,2}}{nama_field}\*{{0,2}}:\s*(.+)", teks, re.IGNORECASE)
    return m.group(1).strip(" *") if m else ""


def _ekstrak_saham(teks):
    """Ambil daftar KODE saham saja dari field SAHAM_TERSEBUT (buang
    penjelasan tambahan yang mungkin ikut ditulis Gemini setelah kodenya)."""
    mentah = _ekstrak_field(teks, "SAHAM_TERSEBUT")
    if not mentah or mentah.strip(" -") == "":
        return []
    kode_list = []
    for bagian in mentah.split(","):
        m = re.match(r"\s*([A-Z]{2,6})\b", bagian.strip())
        if m:
            kode_list.append(m.group(1))
    return kode_list


# ==========================================================
# OUTPUT UNTUK WEBSITE (docs/data/*.json)
# ==========================================================
# index.html (di folder docs/) membaca file-file ini langsung lewat
# fetch() di browser -- tidak ada server/backend, murni file statis.
# Nambah field baru di sini otomatis bisa dipakai di index.html tanpa
# perlu ubah skrip Python lain.

def simpan_hasil_json(tanggal, saham_ke_sektor, hasil_terstruktur):
    os.makedirs(DOCS_DATA_DIR, exist_ok=True)

    data_hari_ini = {
        "tanggal": tanggal,
        "dibuat_pada": sekarang_wib().strftime("%Y-%m-%d %H:%M WIB"),
        "saham_disebut": saham_ke_sektor,
        "sektor": hasil_terstruktur,
    }
    path_hari_ini = os.path.join(DOCS_DATA_DIR, f"{tanggal}.json")
    with open(path_hari_ini, "w", encoding="utf-8") as f:
        json.dump(data_hari_ini, f, ensure_ascii=False, indent=2)

    # manifest.json = daftar tanggal yang ada datanya, terbaru duluan.
    # index.html baca ini dulu buat tahu tanggal apa saja yang bisa dipilih.
    path_manifest = os.path.join(DOCS_DATA_DIR, "manifest.json")
    if os.path.exists(path_manifest):
        with open(path_manifest, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        manifest = {"tanggal_tersedia": []}

    if tanggal not in manifest["tanggal_tersedia"]:
        manifest["tanggal_tersedia"].insert(0, tanggal)
    manifest["tanggal_tersedia"].sort(reverse=True)
    manifest["tanggal_tersedia"] = manifest["tanggal_tersedia"][:60]  # simpan 60 hari terakhir saja

    with open(path_manifest, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\n[SUKSES] Data hari ini disimpan ke {path_hari_ini} (dibaca website).")


def jalankan_analisis_ai():
    if not GEMINI_API_KEY:
        print(
            "[ERROR] Environment variable GEMINI_API_KEY belum di-set. "
            "Lihat komentar di bagian atas file ini untuk cara set-nya."
        )
        return

    if not os.path.exists(FILE_DATASET_BERITA):
        print(f"[ERROR] File {FILE_DATASET_BERITA} tidak ditemukan. Jalankan extract_berita.py dulu.")
        return

    print("Membaca data berita...")
    df = pd.read_csv(FILE_DATASET_BERITA)

    if df.empty:
        print("[WARNING] Dataset berita kosong.")
        return

    # 1. FILTER KHUSUS DATA HARI INI (WIB)
    hari_ini = hari_ini_wib()
    df["Tanggal"] = df["Tanggal"].astype(str)
    df_hari_ini = df[df["Tanggal"] == hari_ini].copy()

    print("\n=============================================")
    print(f"   GEMINI PASSIVE MARKET ANALYST (HARI INI: {hari_ini})")
    print("=============================================\n")

    if df_hari_ini.empty:
        print(f"[PERINGATAN] Tidak ada berita yang diekstrak untuk tanggal hari ini ({hari_ini}).")
        print("Sistem kekurangan data total untuk analisis hari ini.")
        return

    client = genai.Client(api_key=GEMINI_API_KEY)

    # 2. PROSES SEMUA BERITA (Memisahkan Sektor yang Multi-label)
    df_hari_ini["Sektor"] = df_hari_ini["Sektor"].astype(str).str.split(", ")
    df_exploded = df_hari_ini.explode("Sektor")

    hasil_per_sektor = []      # teks lengkap tiap sektor -- dicetak belakangan, di bawah highlight
    saham_ke_sektor = {}       # {"BBCA": ["Keuangan (NAIK)", "Umum/Makro (NETRAL)"]}
    hasil_terstruktur = []     # versi terstruktur -- disimpan ke docs/data/{tanggal}.json

    for sektor in SEKTOR_TARGET:
        berita_sektor = df_exploded[df_exploded["Sektor"] == sektor]

        if berita_sektor.empty:
            hasil_per_sektor.append(
                f"SEKTOR: {sektor}\nPREDIKSI: TIDAK ADA DATA\n"
                f"STATUS: Sektor ini kekurangan data berita khusus untuk hari ini."
            )
            hasil_terstruktur.append({"nama": sektor, "status": "tidak_ada_data", "prediksi": None})
            continue

        teks_berita = ""
        for idx, row in berita_sektor.head(7).iterrows():
            teks_berita += f"Judul: {row['Judul']}\nIsi: {row['Isi Berita'][:600]}...\n---\n"

        prompt = f"""
        Anda adalah seorang Analis Finansial Senior khusus Pasar Saham Indonesia (BEI/IDX).
        Tugas Anda adalah menganalisis kumpulan seluruh berita hari ini untuk kategori/sektor: {sektor}.

        Kumpulan Berita Hari Ini:
        {teks_berita}

        Berikan keputusan final apakah indeks Sektor {sektor} kemungkinan besar akan NAIK, TURUN, atau SIDEWAYS/NETRAL pada keesokan hari perdagangan berdasarkan sentimen berita tersebut.

        PENTING: Periksa apakah di dalam kumpulan berita tersebut menyebutkan nama perusahaan atau kode saham spesifik yang tercatat di bursa (contoh: BBCA, TLKM, GOTO, BBNI, dll). Jika ada, berikan analisis khusus mengenai dampak sentimen berita terhadap pergerakan harga saham perusahaan tersebut secara individual.

        Berikan output dengan format persis seperti ini (jangan memberikan teks pembuka atau penutup lain):
        SEKTOR: {sektor}
        PREDIKSI: [NAIK / TURUN / NETRAL]
        KEY SENTIMENT: [1 kalimat ringkas poin utama berita hari ini]
        ALASAN LOGIS SEKTOR: [Penjelasan singkat 2-3 kalimat mengapa berita menggerakkan sektor secara keseluruhan]
        ANALISIS SAHAM KHUSUS: [Jika tidak ada nama saham spesifik disebut, tulis "Tidak ada saham spesifik yang disebutkan". Jika ada, sebutkan nama/kode sahamnya dan jelaskan dampak sentimen terhadap saham tersebut dalam 2-3 kalimat.]
        SAHAM_TERSEBUT: [daftar KODE saham SAJA dipisah koma, tanpa penjelasan tambahan, contoh: BBCA, TLKM. Kalau tidak ada saham spesifik yang disebut, tulis tanda strip: -]
        """

        try:
            hasil = panggil_gemini_dengan_retry(client, prompt)
            hasil_per_sektor.append(hasil)

            prediksi = _ekstrak_field(hasil, "PREDIKSI") or None
            for kode in _ekstrak_saham(hasil):
                saham_ke_sektor.setdefault(kode, []).append(f"{sektor} ({prediksi})")

            hasil_terstruktur.append({
                "nama": sektor,
                "status": "ok",
                "prediksi": prediksi,
                "key_sentiment": _ekstrak_field(hasil, "KEY SENTIMENT"),
                "alasan": _ekstrak_field(hasil, "ALASAN LOGIS SEKTOR"),
                "analisis_saham": _ekstrak_field(hasil, "ANALISIS SAHAM KHUSUS"),
            })

        except Exception as e:
            hasil_per_sektor.append(
                f"SEKTOR: {sektor}\n[ERROR] Gagal menganalisis setelah {MAX_PERCOBAAN}x percobaan: {e}"
            )
            hasil_terstruktur.append({"nama": sektor, "status": "error", "prediksi": None})

    # 3. HIGHLIGHT: saham spesifik yang disebut hari ini, dicetak DI PALING ATAS
    print("=============================================")
    print("   SAHAM SPESIFIK YANG DISEBUT HARI INI")
    print("=============================================")
    if saham_ke_sektor:
        for kode, kemunculan in sorted(saham_ke_sektor.items()):
            print(f"  * {kode}  ->  {', '.join(kemunculan)}")
    else:
        print("  Tidak ada saham spesifik yang disebut di berita hari ini.")
    print("=============================================\n")

    # 4. RINCIAN LENGKAP PER SEKTOR (persis seperti sebelumnya)
    for teks in hasil_per_sektor:
        print(teks)
        print("\n" + "=" * 45 + "\n")

    # 5. SIMPAN VERSI TERSTRUKTUR UNTUK WEBSITE
    simpan_hasil_json(hari_ini, saham_ke_sektor, hasil_terstruktur)


if __name__ == "__main__":
    jalankan_analisis_ai()
