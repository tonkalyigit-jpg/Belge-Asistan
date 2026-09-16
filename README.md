# Belge Asistanı

PDF belgeleri okuyup özetleyen ve sorulara **yalnızca belgelere dayanarak**,
sayfa numarasıyla cevap veren asistan. Dijital PDF'ler, temiz ve kötü taramalar
okunur. Belgede olmayan bir şey sorulursa uydurmaz, bulamadığını söyler.

## Kurulum

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
echo "GEMINI_API_KEY=..." > .env && chmod 600 .env
```

## Çalıştırma

```bash
cd web && npm install && npm run build && cd ..        # arayüzü derle (bir kez)
.venv/bin/python -m uvicorn api.server:app --port 8000 # http://localhost:8000
```

Tek süreç: FastAPI hem HTTP API'yi hem derlenmiş React arayüzünü veriyor.

Arayüz üzerinde çalışırken Vite'ın anlık yenilemesi daha rahat — o zaman iki
süreç:

```bash
.venv/bin/python -m uvicorn api.server:app --port 8000   # API
cd web && npm run dev                                    # http://localhost:5173
```

Vite `/api` isteklerini 8000'e taşıyor (`web/vite.config.ts`), yani kod iki
modda da aynı yolu kullanıyor.

## Mimari

Arayüz React ve **tarayıcıda** çalışıyor; boru hattı (`core/`, `belge/`,
`memory/`), BGE-M3 ve SQLite ise **Python'da**. İkisinin arasındaki sınır
`api/server.py`: belgeler, sohbetler, oy ve sayfa görüntüsü için JSON;
cevap ile yükleme ilerlemesi için SSE (`/api/sor`, `/api/belgeler/...`).

React tarafı `web/src/`; arayüz metinleri `web/src/sozluk.ts` içinde.

> Daha önce bir Streamlit arayüzü de vardı (`app.py`). Arayüzü Python'da
> olduğu için API'ye ihtiyaç duymuyordu, ama iki arayüzü ayakta tutmak aynı
> veritabanına ve aynı vektör indeksine yazan iki süreç demekti: canlıda
> "database is locked" ve bayat indeks sorunları çıkardı. Kaldırıldı.

## Akış

**Yükleme** (`belge/ingest.py`)

```
PDF -> sayfa sayfa metin katmanı
    -> metni yetersiz sayfa: görüntü -> ön işleme -> görsel model (OCR)
    -> madde/bölüm sınırını ve sayfa aralığını koruyan chunk'lar
    -> belge özeti (başlık, tür, sayfalı öne çıkanlar)
    -> hibrit indeks (BGE-M3 + BM25)
```

Aynı içerik farklı adla ikinci kez işlenmez (sha256). Tek sayfanın okunamaması
belgeyi düşürmez; o sayfa uyarıyla boş kalır.

**Soru** (`core/graph.py`)

| kategori | yol |
|---|---|
| `belge_ici` | adı geçen belge küçükse **tamamı** okunur; değilse ara -> puanla -> (yeniden yaz) -> üret -> hesap, halüsinasyon ve yeterlilik denetimi |
| `capraz_belge` | belgeler bağlama sığıyorsa **tamamı** okunur, sığmıyorsa belge başına ayrı arama |
| `belge_ozeti` | yüklemede üretilen özet — model çalışmaz |
| `kapsam_disi` | kibar ret |

Tam okuma bilinçli: sözleşmede cevabın girdileri farklı maddelerde duruyor
(ceza oranı Madde 5, bedel Madde 3) ve arama birini kaçırınca model hesap
yapamıyordu; uygunluk kontrolünde de hangi maddenin karşılaştırılacağı önceden
bilinemiyor.

**Hesaplar kodla denetleniyor.** Model her adımı `Hesap: 4.850.000 TL × ‰3 × 40
= 582.000 TL` biçiminde yazıyor; `core/nodes/hesap.py` satırı yeniden hesaplıyor
ve tutmazsa cevap düzeltme ipucuyla yeniden üretiliyor.

**Önbellek yok.** Belge kümesi değişiyor ve sohbet bir belgeye odaklanabiliyor;
aynı soru metni bu koşullara göre farklı cevap gerektiriyor.

**Belgeye odak:** kenar çubuğunda belgeye tıklayıp "Bu belgeye sor" dendiğinde
arama yalnızca o belgede yapılıyor.

## Ölçüm

```bash
python scripts/ornek_pdf_uret.py   # sentetik belgeler: dijital, temiz tarama, kötü tarama
python scripts/ocr_olc.py          # karakter / kelime / Türkçe işaret hata oranı
python scripts/zor_sorular.py      # 19 zorlayıcı soru, doğru cevaplarıyla (LLM çağrısı yapar)
```

Parçalama kuralı değişirse belgeleri yeniden yüklemek gerekmiyor — OCR ve özet
tekrarlanmadan saklı sayfa metninden yeniden parçalanıyor (uygulama kapalıyken):

```bash
python scripts/yeniden_parcala.py
```

Sonuçlar ve eşiklerin gerekçesi `config/settings.yaml` yorumlarında. "ÖLÇÜLECEK"
işaretli değerler İngilizce korpustan taşındı; Türkçe belgelerle doğrulanmadı.

## Model değiştirmek

Tüm model kararı `config/settings.yaml` → `tiers`. Dört kademe: `cheap`
(kontrol düzlemi), `writer`, `strong` (özet ve çapraz sentez), `vision` (OCR).
`openai_compat` vLLM ve Ollama'yı da kapsıyor; local modele geçiş kademe başına
üç satır (`base_url`, `model`, `api_key_env`).

Gemini ücretsiz katmanında gönderilen içerik Google'ın ürünlerini geliştirmek
için kullanılabilir. Gerçek kurumsal evrakla çalışmadan önce local modele geçin.

## Test

```bash
.venv/bin/python -m pytest
```

LLM ve embedding sahte; PDF'ler gerçek, her koşuda sentetik metinlerden üretilir.
