/** API istemcisi.
 *
 *  Cevap ve yükleme akışları SSE. `EventSource` KULLANILMIYOR: yalnızca GET
 *  destekliyor, soru gövdesi ise POST ile gidiyor (uzun metin, odak, kip).
 *  `fetch` + `ReadableStream` ikisini de karşılıyor ve iptal edilebiliyor.
 */
import type { Belge, Durum, Mesaj, SohbetOzeti, Sonuc, YuklemeSonucu } from "./types";

// Varsayılan AYNI KAYNAK: derlenmiş arayüz API ile aynı sunucudan veriliyor,
// yani adres neyse istek de oraya gidiyor (başka bir makineye kurulduğunda da
// çalışır). Geliştirmede Vite 5173'te koşuyor ve `/api` isteklerini vite
// yapılandırmasındaki proxy 8000'e taşıyor.
const KOK = import.meta.env.VITE_API ?? "";

async function al<T>(yol: string, secenek?: RequestInit): Promise<T> {
  const cevap = await fetch(`${KOK}${yol}`, secenek);
  if (!cevap.ok) throw new Error(`${cevap.status} ${cevap.statusText}`);
  return (await cevap.json()) as T;
}

export const api = {
  durum: () => al<Durum>("/api/durum"),
  belgeler: () => al<Belge[]>("/api/belgeler"),
  belgeSil: (id: number) => al<unknown>(`/api/belgeler/${id}`, { method: "DELETE" }),
  sayfaUrl: (belgeId: number, sayfa: number) =>
    `${KOK}/api/belgeler/${belgeId}/sayfa/${sayfa}`,
  sohbetler: () => al<SohbetOzeti[]>("/api/sohbetler"),
  sohbetAc: () => al<{ id: number }>("/api/sohbetler", { method: "POST" }),
  sohbet: (id: number) => al<{ id: number; mesajlar: Mesaj[] }>(`/api/sohbetler/${id}`),
  sohbetSil: (id: number) => al<unknown>(`/api/sohbetler/${id}`, { method: "DELETE" }),
  oy: (govde: Record<string, unknown>) =>
    al<unknown>("/api/oy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(govde),
    }),
};

/** SSE gövdesini olay olay okur. */
async function* olaylar(cevap: Response): AsyncGenerator<{ ad: string; veri: any }> {
  const okuyucu = cevap.body!.getReader();
  const cozucu = new TextDecoder();
  let tampon = "";
  while (true) {
    const { done, value } = await okuyucu.read();
    if (done) break;
    tampon += cozucu.decode(value, { stream: true });
    // Olaylar boş satırla ayrılıyor; yarım kalan son parça tamponda bekliyor.
    const parcalar = tampon.split("\n\n");
    tampon = parcalar.pop() ?? "";
    for (const parca of parcalar) {
      let ad = "message";
      const satirlar: string[] = [];
      for (const satir of parca.split("\n")) {
        if (satir.startsWith("event:")) ad = satir.slice(6).trim();
        else if (satir.startsWith("data:")) satirlar.push(satir.slice(5).trim());
      }
      if (satirlar.length) yield { ad, veri: JSON.parse(satirlar.join("\n")) };
    }
  }
}

export interface SoruGeri {
  onAdim: (ad: string) => void;
  onParca: (metin: string) => void;
  onSohbet: (id: number) => void;
  onSon: (cevap: string, sonuc: Sonuc) => void;
  onHata: (mesaj: string) => void;
}

export async function sor(
  govde: {
    soru: string;
    sohbet_id: number | null;
    kip: string;
    odak_belge_id: number | null;
    yeniden?: boolean;
  },
  geri: SoruGeri,
  iptal?: AbortSignal,
): Promise<void> {
  const cevap = await fetch(`${KOK}/api/sor`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(govde),
    signal: iptal,
  });
  if (!cevap.ok || !cevap.body) {
    geri.onHata(`Sunucu ${cevap.status}`);
    return;
  }
  for await (const olay of olaylar(cevap)) {
    if (olay.ad === "adim") geri.onAdim(olay.veri.ad);
    else if (olay.ad === "parca") geri.onParca(olay.veri.metin);
    else if (olay.ad === "sohbet") geri.onSohbet(olay.veri.sohbet_id);
    else if (olay.ad === "son") geri.onSon(olay.veri.cevap, olay.veri.sonuc);
    else if (olay.ad === "hata") geri.onHata(olay.veri.mesaj);
  }
}

export async function yukle(
  dosya: File,
  geri: {
    onAsama: (asama: string, i: number, n: number) => void;
    onBitti: (sonuc: YuklemeSonucu) => void;
    onHata: (mesaj: string) => void;
  },
): Promise<void> {
  const govde = new FormData();
  govde.append("dosya", dosya);
  const baslangic = await fetch(`${KOK}/api/belgeler`, { method: "POST", body: govde });
  if (!baslangic.ok) {
    geri.onHata(`Sunucu ${baslangic.status}`);
    return;
  }
  const { is_id } = (await baslangic.json()) as { is_id: string };
  const akis = await fetch(`${KOK}/api/belgeler/yukleme/${is_id}`);
  if (!akis.ok || !akis.body) {
    geri.onHata("Yükleme akışı açılamadı");
    return;
  }
  for await (const olay of olaylar(akis)) {
    if (olay.ad === "asama") geri.onAsama(olay.veri.asama, olay.veri.i, olay.veri.n);
    else if (olay.ad === "bitti") geri.onBitti(olay.veri.sonuc);
    else if (olay.ad === "hata") geri.onHata(olay.veri.mesaj);
  }
}
