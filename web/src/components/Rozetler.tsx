/** Kararların telemetrisi.
 *
 *  Sıra bilinçli: önce YOL (cevap nereden geldi), sonra maliyet ve süre, en
 *  sonda yalnızca gerçekleşmişse istisnalar. Hepsi monospace ve küçük: bunlar
 *  modelin yazdığı metin değil, sistemin ölçtüğü veri — kullanıcı ikisini bir
 *  bakışta ayırabilmeli.
 *
 *  Rozetler MESAJIN KENDİ dilinde: sohbette dil değişince geriye kaydırılan
 *  eski mesajların rozetleri değişmemeli.
 */
import type { Sonuc } from "../types";
import { KATEGORI, T, YOL, buyukHarf, type Dil } from "../sozluk";

type Rozet = [sinif: string, metin: string];

export function Rozetler({ sonuc }: { sonuc: Sonuc }) {
  const dil: Dil = sonuc.lang === "en" ? "en" : "tr";
  const t = T[dil];
  const i = dil === "tr" ? 0 : 1;
  const rozetler: Rozet[] = [];

  if (sonuc.kind === "yukleme") {
    rozetler.push(["birincil", "BELGE YÜKLENDİ"]);
    rozetler.push(["", `${sonuc.page_count ?? 0} ${t.pages}`]);
    if (sonuc.ocr_pages) rozetler.push(["guclu", `${sonuc.ocr_pages} OCR`]);
    rozetler.push(["", `${((sonuc.total_ms ?? 0) / 1000).toFixed(1)}s`]);
    return <Sira rozetler={rozetler} />;
  }

  rozetler.push(["birincil", buyukHarf((YOL[sonuc.route] ?? ["?", "?"])[i], dil)]);

  if (sonuc.scope_titles?.length) {
    // Geriye kaydırıldığında hangi cevabın hangi belgeye sorulduğu görünmeli;
    // odak çipi yalnızca GÜNCEL durumu gösteriyor.
    const ad = sonuc.scope_titles[0];
    rozetler.push(["guclu", `${t.focus_badge}: ${ad.length <= 28 ? ad : ad.slice(0, 27) + "…"}`]);
  }

  rozetler.push(["", (KATEGORI[sonuc.category] ?? ["?", "?"])[i].toLocaleLowerCase(dil)]);
  if (sonuc.full_context) rozetler.push(["guclu", "tam okuma"]);

  if (sonuc.model_used) {
    const kisa = sonuc.model_used.split("/").pop();
    rozetler.push([
      sonuc.tier_used === "strong" ? "guclu" : "",
      `${kisa}  ·  ${t.difficulty} ${sonuc.difficulty}/5`,
    ]);
  }

  rozetler.push(["", `$${sonuc.cost_usd.toFixed(4)}`]);
  rozetler.push(["", `${(sonuc.total_ms / 1000).toFixed(1)}s`]);

  // Hız sınırı beklemesi ayrı: bu süre modelin düşünmesi değil, sağlayıcının
  // dakikalık kotasının dolması. Ayrılmadığında sistem donmuş görünüyor.
  if (sonuc.rate_limit_wait_s) {
    rozetler.push(["uyari", `${sonuc.rate_limit_wait_s.toFixed(0)}s ${t.rate_limited}`]);
  }
  if (sonuc.rewrites) rozetler.push(["", `↻ ${sonuc.rewrites} ${t.rewrites}`]);
  if (sonuc.regens) rozetler.push(["", `⟳ ${sonuc.regens} ${t.regens}`]);
  if (sonuc.low_confidence) rozetler.push(["uyari", t.low_conf]);

  return <Sira rozetler={rozetler} />;
}

function Sira({ rozetler }: { rozetler: Rozet[] }) {
  return (
    <div className="rozet-sira">
      {rozetler.map(([sinif, metin], n) => (
        <span key={n} className={`rozet ${sinif}`}>{metin}</span>
      ))}
    </div>
  );
}
