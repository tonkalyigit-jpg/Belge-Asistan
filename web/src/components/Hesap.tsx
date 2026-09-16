/** Sağ üstteki hesap menüsü: kim olduğun, ayarlar ve çıkış.
 *
 *  Kenar çubuğunun dibinde duruyordu; orada sohbet listesi uzadıkça aşağı
 *  kayıyor ve görünmez oluyordu. Sağ üst köşe, hesabın her ekranda aynı yerde
 *  durduğu alışılmış yer — paylaşılan bir makinede "hangi hesapla açığım"
 *  sorusu bir bakışta cevaplanmalı.
 */
import { useEffect, useRef, useState } from "react";
import type { Kullanici } from "../types";
import { Ikon } from "./Ogeler";

export function Hesap({ ben, onAyarlar, onCikis }: {
  ben: Kullanici;
  onAyarlar: () => void;
  onCikis: () => void;
}) {
  const [acik, setAcik] = useState(false);
  const sarmal = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!acik) return;
    function disari(olay: MouseEvent) {
      if (!sarmal.current?.contains(olay.target as Node)) setAcik(false);
    }
    function esc(olay: KeyboardEvent) {
      if (olay.key === "Escape") setAcik(false);
    }
    document.addEventListener("mousedown", disari);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", disari);
      document.removeEventListener("keydown", esc);
    };
  }, [acik]);

  return (
    <div className="hesap-kose" ref={sarmal}>
      <button className="hesap-hap" aria-haspopup="menu" aria-expanded={acik}
              onClick={() => setAcik((a) => !a)}>
        <Ikon ad="account_circle" />
        <span className="hesap-hap-ad">{ben.ad}</span>
        <Ikon ad="expand_more" style={{ fontSize: ".85rem" }} />
      </button>

      {acik && (
        <div className="hesap-menu" role="menu">
          <div className="hesap-menu-bas">
            <strong>{ben.ad}</strong>
            <span>@{ben.username} · {ben.rol === "admin" ? "yönetici" : "kullanıcı"}</span>
          </div>
          {ben.rol === "admin" && (
            <button role="menuitem" onClick={() => { setAcik(false); onAyarlar(); }}>
              <Ikon ad="settings" /> Ayarlar
            </button>
          )}
          <button role="menuitem" onClick={() => { setAcik(false); onCikis(); }}>
            <Ikon ad="logout" /> Çıkış
          </button>
        </div>
      )}
    </div>
  );
}
