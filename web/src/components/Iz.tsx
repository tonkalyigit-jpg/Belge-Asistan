/** "Bu cevap nasıl üretildi" — kullanıcıya verilen hesap.
 *
 *  Hata ayıklama çıktısı DEĞİL: kodun iç adları yerine ne yapıldığını söyleyen
 *  cümleler var, sayılar ikinci planda ve sönük. İnce çubuk hangi adımın
 *  zamanı yediğini okumadan gösteriyor.
 */
import type { Adim, Sonuc } from "../types";
import { ADIM, ADIM_ALAN, KATEGORI_DUZ, T, type Dil } from "../sozluk";
import { Katlanir } from "./Ogeler";

function detay(adim: Adim): string {
  const parcalar: string[] = [];
  for (const [alan, deger] of Object.entries(adim)) {
    if (["node", "ms", "cost_usd", "tokens"].includes(alan)) continue;
    let yazi: string;
    if (alan === "category") yazi = KATEGORI_DUZ[String(deger)] ?? String(deger);
    else if (alan === "difficulty") yazi = `${deger}/5`;
    else if (alan === "context_tokens") yazi = `${deger} token`;
    else if (alan === "query") yazi = `“${deger}”`;
    else if (alan === "regen" && !deger) continue;   // sıfırıncı üretim bilgi değil
    else yazi = String(deger);
    parcalar.push(`${ADIM_ALAN[alan] ?? alan}: ${yazi}`);
  }
  return parcalar.join("  ·  ");
}

export function Iz({ sonuc }: { sonuc: Sonuc }) {
  const adimlar = sonuc.steps ?? [];
  if (!adimlar.length) return null;
  const dil: Dil = sonuc.lang === "en" ? "en" : "tr";
  const t = T[dil];
  const toplam = adimlar.reduce((n, a) => n + a.ms, 0) || 1;

  return (
    <Katlanir baslik={`${t.trace} — ${adimlar.length} ${t.trace_steps}`} cocuk={
      <>
        {adimlar.map((adim, n) => {
          const [ad, aciklama] = ADIM[adim.node] ?? [adim.node, ""];
          const metin = detay(adim);
          return (
            <div className="iadim" key={n}>
              <div className="iadim-bas">
                <span className="iadim-ad">{ad}</span>
                <span className="iadim-sayi">
                  {(adim.ms / 1000).toFixed(1)} sn · {adim.cost_usd ? `$${adim.cost_usd.toFixed(4)}` : "—"}
                </span>
              </div>
              <div className="ibar">
                <i style={{ width: `${Math.max(2, Math.round((adim.ms / toplam) * 100))}%` }} />
              </div>
              <div className="iadim-neden">
                {aciklama}
                {metin ? <><br /><span className="iadim-detay">{metin}</span></> : null}
              </div>
            </div>
          );
        })}
        {sonuc.warnings?.length ? (
          <div className="inotlar">
            {sonuc.warnings.map((uyari, n) => <div key={n}>› {uyari}</div>)}
          </div>
        ) : null}
      </>
    } />
  );
}
