/** API'nin döndürdüğü şekiller. Python tarafındaki `QueryState.to_dict()` ve
 *  `ingest.listele()` ile birebir; alan adları bilerek Türkçe/İngilizce karışık,
 *  çünkü çekirdeğin kendi adlandırması öyle ve iki tarafı ayrıştırmak sessiz
 *  hatalara davetiye. */

export interface Kullanici {
  id: number;
  username: string;
  ad: string;
  rol: "admin" | "user";
  aktif: boolean;
  created_at: string;
  last_login: string | null;
}

/** Admin panelindeki kullanım satırı. Soru METNİ yok: admin kimin ne
 *  sorduğunu değil, ne kadar kullandığını görüyor. */
export interface KullanimSatiri {
  id: number;
  username: string;
  display_name: string | null;
  role: string;
  last_login: string | null;
  sorgu: number;
  maliyet: number;
  ort_ms: number;
  belge: number;
  sohbet: number;
}

export interface Belge {
  id: number;
  owner_id: number | null;
  paylasim: "ozel" | "ortak";
  filename: string;
  title: string | null;
  page_count: number;
  ocr_pages: number;
  status: "ready" | "processing" | "failed" | "deleted";
  error: string | null;
  summary: string | null;
  added_at: string;
}

export interface Pasaj {
  section: string;
  chunk_kind: string;
  page_start: number | null;
  page_end: number | null;
  text: string;
  truncated: boolean;
}

export interface Atif {
  n: number;
  kind: string;
  document_id: number;
  title: string;
  filename: string;
  score: number;
  passages: Pasaj[];
}

export interface Adim {
  node: string;
  ms: number;
  cost_usd: number;
  tokens?: number;
  [alan: string]: unknown;
}

/** Bir cevabın telemetrisi — arayüzdeki rozetler ve iz paneli bunu okuyor. */
export interface Sonuc {
  query?: string;
  standalone_query?: string;
  lang?: "tr" | "en";
  category: string;
  route: string;
  difficulty: number;
  model_used: string | null;
  tier_used: string | null;
  cost_usd: number;
  total_ms: number;
  rewrites: number;
  regens: number;
  low_confidence: boolean;
  rate_limit_wait_s?: number;
  full_context?: boolean;
  scope_titles?: string[];
  citations?: Atif[];
  steps?: Adim[];
  warnings?: string[];
  /** Yükleme mesajlarında dolu: bunlar soru-cevap değil, belge kaydı. */
  kind?: "yukleme";
  page_count?: number;
  ocr_pages?: number;
  chunks?: number;
}

export interface Mesaj {
  role: "user" | "assistant";
  content: string;
  result?: Sonuc | null;
  voted?: string | null;
}

export interface SohbetOzeti {
  id: number;
  title: string;
  updated_at: string;
  n: number;
}

export interface Durum {
  kullanici: Kullanici;
  isinma: { bitti: boolean; saniye: number; hata: string };
  maliyet: {
    queries: number;
    total_cost: number;
    avg_cost: number;
    by_route: Record<string, number>;
  };
  modeller: Record<
    string,
    { provider: string; model: string; price_in: number; price_out: number }
  >;
  kipler: Record<string, string>;
}

export interface YuklemeSonucu {
  belge_id: number | null;
  durum: "ready" | "duplicate" | "failed";
  baslik: string;
  dosya: string;
  sayfa: number;
  ocr_sayfa: number;
  chunk: number;
  ozet: string;
  saniye: number;
  uyarilar: string[];
  hata: string;
}
