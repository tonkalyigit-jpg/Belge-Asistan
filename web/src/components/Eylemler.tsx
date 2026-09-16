/** Cevabın altındaki eylem sırası: beğen · beğenme · yeniden üret · kopyala.
 *
 *  Rozetler gibi küçük ve sessiz duruyorlar — cevabın kendisiyle yarışmamalı.
 *  Ama "sessiz" ile "erişilemez" farklı şeyler: buton KUTUSU 40px (dokunma
 *  hedefi), ikon küçük ve sönük (görsel sessizlik). `title` aynı zamanda
 *  erişilebilir ad görevi görüyor — yalnızca ikon taşıyan bir butonun ekran
 *  okuyucuya söyleyeceği tek şey o.
 */
import { useState } from "react";
import type { Mesaj, Sonuc } from "../types";
import { T, type Dil } from "../sozluk";
import { Ikon } from "./Ogeler";

export function Eylemler({ mesaj, sonMu, onOy, onYeniden }: {
  mesaj: Mesaj;
  sonMu: boolean;
  onOy: (yon: 1 | -1, etiket: string) => void;
  onYeniden: () => void;
}) {
  const sonuc = (mesaj.result ?? {}) as Sonuc;
  const dil: Dil = sonuc.lang === "en" ? "en" : "tr";
  const t = T[dil];
  const [kopyalandi, setKopyalandi] = useState(false);
  const oylandi = mesaj.voted ?? null;
  // Oy, sınıflandırıcının örnek havuzuna gidiyor: kaynağı olan her gerçek
  // cevap oylanabilir.
  const oylanabilir = Boolean(sonuc.query);

  async function kopyala() {
    try {
      await navigator.clipboard.writeText(mesaj.content);
      setKopyalandi(true);
      setTimeout(() => setKopyalandi(false), 2000);
    } catch {
      setKopyalandi(false);
    }
  }

  return (
    <>
      <div className="eylemler">
        <button title={t.helpful_up} aria-label={t.helpful_up}
                disabled={!oylanabilir || Boolean(oylandi)}
                onClick={() => onOy(1, t.thanks_up)}>
          <Ikon ad="thumb_up" dolu={oylandi === t.thanks_up} />
        </button>
        <button title={t.helpful_down} aria-label={t.helpful_down}
                disabled={!oylanabilir || Boolean(oylandi)}
                onClick={() => onOy(-1, t.thanks_down)}>
          <Ikon ad="thumb_down" dolu={oylandi === t.thanks_down} />
        </button>
        {/* Yeniden üretim yalnızca SON cevap için: ortadaki bir cevabı
            değiştirmek sonraki turların dayandığı zemini kaydırır. */}
        <button title={t.regen} aria-label={t.regen} disabled={!sonMu} onClick={onYeniden}>
          <Ikon ad="refresh" />
        </button>
        <button title={t.copy} aria-label={t.copy} onClick={kopyala}>
          <Ikon ad="content_copy" />
        </button>
      </div>
      {kopyalandi ? <div className="eylem-not">{t.copied}</div> : null}
      {oylandi ? <div className="eylem-not">{oylandi}</div> : null}
    </>
  );
}
