/** Alt çubuk: odak çipi, kip hapı, soru kutusu.
 *
 *  Üçü de altta sabit duruyor ve birlikte hareket ediyor. Kip hapı giriş
 *  kutusunun SAĞ üstünde, küçük ve sessiz: her soruda değiştirilecek bir ayar
 *  değil, gerektiğinde hatırlanacak bir seçenek — boyutu bunu söylemeli.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { T } from "../sozluk";
import { Ikon } from "./Ogeler";

const t = T.tr;

/** Etiket AMACI söylüyor, model adını değil: ücretsiz kurulumda iki kademe de
 *  aynı modele bağlı ve adı öne koyunca menüde birebir aynı iki satır çıkıyor. */
const KIPLER: Record<string, [string, string]> = {
  writer: ["Hızlı", "En hızlı yanıtlar"],
  strong: ["Derin analiz", "Karmaşık sorunları çözme"],
};

export function Alt({ kip, setKip, odakAdi, onOdakKaldir, calisiyor, onGonder, modelAdlari }: {
  kip: string;
  setKip: (k: string) => void;
  odakAdi: string | null;
  onOdakKaldir: () => void;
  calisiyor: boolean;
  onGonder: (soru: string) => void;
  modelAdlari: Record<string, string>;
}) {
  const [metin, setMetin] = useState("");
  const [menuAcik, setMenuAcik] = useState(false);
  const alan = useRef<HTMLTextAreaElement>(null);
  const sarmal = useRef<HTMLDivElement>(null);

  // Kutu içeriğe göre büyüyor; sabit yükseklik uzun soruyu gizliyordu.
  useLayoutEffect(() => {
    const el = alan.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
  }, [metin]);

  // Menü dışarı tıklanınca ve Esc ile kapanıyor.
  useEffect(() => {
    if (!menuAcik) return;
    function disari(olay: MouseEvent) {
      if (!sarmal.current?.contains(olay.target as Node)) setMenuAcik(false);
    }
    function esc(olay: KeyboardEvent) {
      if (olay.key === "Escape") setMenuAcik(false);
    }
    document.addEventListener("mousedown", disari);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", disari);
      document.removeEventListener("keydown", esc);
    };
  }, [menuAcik]);

  function gonder() {
    const soru = metin.trim();
    if (!soru || calisiyor) return;
    setMetin("");
    onGonder(soru);
  }

  const kisaOdak = odakAdi && odakAdi.length > 42 ? odakAdi.slice(0, 41) + "…" : odakAdi;
  const yerTutucu = odakAdi
    ? t.placeholder_focus.replace("{title}", odakAdi.length > 40 ? odakAdi.slice(0, 39) + "…" : odakAdi)
    : t.placeholder;

  return (
    <div className="alt">
      <div className="alt-ust">
        {odakAdi && (
          <button className="odak-cip" title={t.focus_clear} onClick={onOdakKaldir}>
            <span className="ad">{t.focus_only}: {kisaOdak}</span>
            <Ikon ad="close" style={{ fontSize: ".9rem" }} />
          </button>
        )}
        <div className="kip-sarmal" ref={sarmal}>
          <button className="kip-hap" aria-haspopup="menu" aria-expanded={menuAcik}
                  onClick={() => setMenuAcik((a) => !a)}>
            {KIPLER[kip][0]} <Ikon ad="expand_more" style={{ fontSize: ".85rem" }} />
          </button>
          {menuAcik && (
            <div className="kip-menu" role="menu">
              {Object.entries(KIPLER).map(([anahtar, [ad, neden]]) => (
                <button key={anahtar} role="menuitem"
                        onClick={() => { setKip(anahtar); setMenuAcik(false); }}>
                  <strong>{anahtar === kip ? "✓ " : "  "}{ad}</strong>
                  <span className="aciklama">
                    {(modelAdlari[anahtar] ?? "").split("/").pop()} · {neden}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="soru-kutu">
        <textarea
          ref={alan}
          rows={1}
          value={metin}
          placeholder={yerTutucu}
          aria-label={yerTutucu}
          onChange={(e) => setMetin(e.target.value)}
          onKeyDown={(e) => {
            // Enter gönderiyor, Shift+Enter satır atlıyor — sohbet kutusunda
            // beklenen davranış bu.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              gonder();
            }
          }}
        />
        <button className="gonder" onClick={gonder} disabled={calisiyor || !metin.trim()}
                title="Gönder" aria-label="Gönder">
          <Ikon ad={calisiyor ? "hourglass_empty" : "arrow_upward"} />
        </button>
      </div>
    </div>
  );
}
