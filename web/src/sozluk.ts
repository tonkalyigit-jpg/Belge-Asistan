/** Arayüz metinleri ve etiketler — Streamlit sürümüyle birebir.
 *
 *  SAYFA ÇATISI HEP TÜRKÇE, YALNIZCA SOHBET ALANI İKİ DİLLİ:
 *  `T.tr` kenar çubuğu ve başlık için; rozetler, kaynaklar ve iz paneli ise
 *  CEVABIN dilinde yazılıyor (sınıflandırıcı dili zaten tespit ediyor, ayrıca
 *  bir dil seçici koymak aynı bilgiyi kullanıcıya iki kez sordurur).
 */

export const T = {
  tr: {
    helpful_up: "Bu yanıt işime yaradı",
    helpful_down: "Bu yanıt işime yaramadı",
    regen: "Yeniden üret",
    copy: "Cevabı kopyala",
    copied: "Kopyalandı.",
    thanks_up: "Teşekkürler — geri bildirim kaydedildi.",
    thanks_down: "Kaydedildi — sınıflandırıcı benzer sorularda bu örneği görecek.",
    low_conf: "doğrulanamadı",
    no_answer: "Yanıt üretilemedi.",
    rate_limited: "kota beklemesi",
    difficulty: "zorluk",
    rewrites: "yeniden yazma",
    regens: "yeniden üretim",
    trace: "Bu cevap nasıl üretildi",
    trace_steps: "adım",
    title: "Belge Asistanı",
    subtitle: "yüklediğiniz belgelerden, sayfa numarasıyla cevap",
    placeholder: "Belgeleriniz hakkında bir soru sorun…",
    sources: "Kaynaklar",
    document_word: "belge",
    passage_word: "bölüm",
    full_read: "tamamı okundu",
    show_page: "Belgedeki sayfayı göster",
    page_word: "Sayfa",
    page_missing: "Sayfa görüntüsü açılamadı.",
    documents: "Belge yükle",
    upload: "PDF seç",
    upload_hint: "ya da sürükleyin",
    pages: "sayfa",
    ocr_pages: "taramadan okundu",
    uploaded: "yüklendi",
    no_documents: "Henüz belge yok. Bir PDF sürükleyin — dijital ya da taranmış.",
    delete_doc: "Belgeyi sil",
    cost: "Maliyet",
    queries: "sorgu",
    avg: "ortalama",
    models: "Modeller",
    focus_ask: "Bu belgeye sor",
    focus_clear: "Odağı kaldır",
    focus_only: "Yalnızca",
    focus_badge: "yalnızca",
    placeholder_focus: "{title} hakkında sorun…",
    doc_uploaded: "Yüklendi",
    doc_type: "Tür",
    doc_pages: "Sayfa",
    doc_file: "Dosya",
  },
  en: {
    helpful_up: "This answer was useful",
    helpful_down: "This answer was not useful",
    regen: "Regenerate",
    copy: "Copy answer",
    copied: "Copied.",
    thanks_up: "Thanks — feedback recorded.",
    thanks_down: "Recorded — the classifier will see this example for similar questions.",
    low_conf: "unverified",
    no_answer: "No answer could be generated.",
    rate_limited: "quota wait",
    difficulty: "difficulty",
    rewrites: "rewrites",
    regens: "regenerations",
    trace: "How this answer was produced",
    trace_steps: "steps",
    title: "Document Assistant",
    subtitle: "answers from your documents, with page references",
    placeholder: "Ask a question about your documents…",
    sources: "Sources",
    document_word: "documents",
    passage_word: "passages",
    full_read: "read in full",
    show_page: "Show the page in the document",
    page_word: "Page",
    page_missing: "The page image could not be opened.",
    documents: "Upload document",
    upload: "Choose PDF",
    upload_hint: "or drag it here",
    pages: "pages",
    ocr_pages: "read from scan",
    uploaded: "uploaded",
    no_documents: "No documents yet. Drop a PDF — digital or scanned.",
    delete_doc: "Delete document",
    cost: "Cost",
    queries: "queries",
    avg: "average",
    models: "Model tiers",
    focus_ask: "Ask this document",
    focus_clear: "Remove focus",
    focus_only: "Only",
    focus_badge: "only",
    placeholder_focus: "Ask about {title}…",
    doc_uploaded: "Uploaded",
    doc_type: "Type",
    doc_pages: "Pages",
    doc_file: "File",
  },
} as const;

export type Dil = keyof typeof T;

export const KATEGORI: Record<string, [string, string]> = {
  belge_ici: ["Belge içi", "In document"],
  capraz_belge: ["Çapraz belge", "Cross-document"],
  belge_ozeti: ["Belge özeti", "Document summary"],
  kapsam_disi: ["Kapsam dışı", "Out of scope"],
};

export const YOL: Record<string, [string, string]> = {
  refusal: ["Kibar ret", "Refusal"],
  ozet: ["Saklı özet", "Stored summary"],
  rag: ["Belgelerden", "From documents"],
};

/** Boru hattı düğümlerinin kullanıcıya görünen adları.
 *  Kodun iç adları ("hallucination_check") burada işe yaramıyor: kullanıcı
 *  sistemin ne yaptığını anlamak için bakıyor, dosya adı öğrenmek için değil. */
export const ADIM: Record<string, [string, string]> = {
  classifier: ["Soru çözümlendi", "Soru türü, zorluk ve ilgili belgeler belirlendi"],
  retrieve: ["Belgelerde arandı", "Belgelerin bölümleri tarandı"],
  grade: ["Bölümler puanlandı", "Bulunanların soruyla ilgisi değerlendirildi"],
  rewrite: ["Arama yenilendi", "Sonuç yetersizdi, sorgu belgenin diliyle yeniden yazıldı"],
  model_router: ["Model seçildi", "Sorunun zorluğuna göre kademe belirlendi"],
  generate: ["Cevap yazıldı", "Bulunan bölümlerden cevap üretildi"],
  calculation_check: ["Hesap denetimi", "Cevaptaki hesaplar yazılımla yeniden yapıldı"],
  hallucination_check: ["Kaynak doğrulaması", "Her ifadenin belgede karşılığı var mı bakıldı"],
  sufficiency_check: ["Yeterlilik kontrolü", "Cevap soruyu gerçekten karşılıyor mu bakıldı"],
  refusal: ["Kibar ret", "Soru yüklenen belgelerle ilgili olmadığı için cevaplanmadı"],
  summary: ["Saklı özet", "Yükleme sırasında çıkarılan özet getirildi, model çalışmadı"],
};

export const ADIM_ALAN: Record<string, string> = {
  query: "arama sorgusu",
  hits: "bulunan bölüm",
  relevant: "ilgili bulunan",
  tier: "kademe",
  context_tokens: "bağlam",
  category: "kategori",
  difficulty: "zorluk",
  documents: "adı geçen belge",
  regen: "yeniden üretim",
  attempt: "deneme",
  checked: "denetlenen hesap",
  wrong: "hatalı",
};

export const KATEGORI_DUZ: Record<string, string> = {
  belge_ici: "tek belgeden cevaplanan soru",
  capraz_belge: "belgeleri birlikte okumayı gerektiren soru",
  belge_ozeti: "belge özeti",
  kapsam_disi: "belgelerle ilgisiz",
};

export const YUKLEME_ASAMA: Record<string, string> = {
  ocr: "taranmış sayfa okunuyor",
  ozet: "özet çıkarılıyor",
  indeks: "indeksleniyor",
};

/** Türkçe büyük harf. `toUpperCase()` Türkçeyi bilmiyor: "Kibar ret" ->
 *  "KIBAR RET" (doğrusu "KİBAR RET"). Önce i/ı eşlemesi, sonra büyük harf. */
export function buyukHarf(metin: string, dil: Dil = "tr"): string {
  const hazir = dil === "tr" ? metin.replace(/i/g, "İ").replace(/ı/g, "I") : metin;
  return hazir.toUpperCase();
}

const AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
  "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];

/** SQLite `datetime('now')` UTC yazıyor; kullanıcıya yerel saatle. */
export function tarih(utcMetin: string): string {
  if (!utcMetin) return "";
  const d = new Date(utcMetin.replace(" ", "T") + "Z");
  if (Number.isNaN(d.getTime())) return utcMetin;
  const ss = String(d.getHours()).padStart(2, "0");
  const dd = String(d.getMinutes()).padStart(2, "0");
  return `${d.getDate()} ${AYLAR[d.getMonth()]} ${d.getFullYear()} · ${ss}:${dd}`;
}

/** Saklı özetin ilk satırındaki "Tür: …" bilgisi. */
export function tur(ozet: string | null): string {
  const ilk = (ozet ?? "").split("\n", 1)[0];
  return ilk.startsWith("Tür:") ? ilk.slice(4).trim() : "";
}
