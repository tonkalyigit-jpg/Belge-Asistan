/** Giriş ekranı.
 *
 *  Kayıt olma yok: hesapları yönetici açıyor. Hata mesajı hangi alanın yanlış
 *  olduğunu SÖYLEMİYOR ("kullanıcı adı ya da parola hatalı") — sunucu da öyle
 *  davranıyor; ayırmak, geçerli kullanıcı adlarını dışarıdan taramaya izin
 *  verir.
 */
import { useState } from "react";
import { api } from "../api";
import type { Kullanici } from "../types";
import { Ikon } from "./Ogeler";

export function Giris({ onGiris }: { onGiris: (k: Kullanici) => void }) {
  const [kullanici, setKullanici] = useState("");
  const [parola, setParola] = useState("");
  const [hata, setHata] = useState("");
  const [bekliyor, setBekliyor] = useState(false);

  async function gonder(olay: React.FormEvent) {
    olay.preventDefault();
    if (!kullanici.trim() || !parola || bekliyor) return;
    setBekliyor(true);
    setHata("");
    try {
      onGiris(await api.giris(kullanici.trim(), parola));
    } catch (e) {
      setHata(e instanceof Error ? e.message : String(e));
      setParola("");
    } finally {
      setBekliyor(false);
    }
  }

  return (
    <div className="giris-ekran">
      <form className="giris-kutu" onSubmit={gonder}>
        <div className="manset">
          <h1>
            <img src="/orion-logo.png" alt="Orion Innovation" />
            Belge Asistanı
          </h1>
          <p>şirket içi belge asistanı</p>
        </div>

        <label className="alan">
          <span>Kullanıcı adı</span>
          <input value={kullanici} onChange={(e) => setKullanici(e.target.value)}
                 autoComplete="username" autoFocus spellCheck={false} />
        </label>

        <label className="alan">
          <span>Parola</span>
          <input type="password" value={parola} onChange={(e) => setParola(e.target.value)}
                 autoComplete="current-password" />
        </label>

        {hata && <p className="giris-hata" role="alert">{hata}</p>}

        <button className="giris-dugme" type="submit" disabled={bekliyor}>
          <Ikon ad={bekliyor ? "hourglass_empty" : "login"} />
          {bekliyor ? "Kontrol ediliyor…" : "Giriş yap"}
        </button>

        <p className="giris-not">
          Hesabınız yoksa yöneticinizden isteyin. Yüklediğiniz belgeleri
          yalnızca siz görürsünüz.
        </p>
      </form>
    </div>
  );
}
