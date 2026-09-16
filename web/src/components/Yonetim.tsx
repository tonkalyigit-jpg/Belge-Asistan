/** Yönetim paneli — hesaplar ve kullanım.
 *
 *  BURADA BELGE İÇERİĞİ VE SOHBET YOK, olmayacak da: seçilen yetki modelinde
 *  yönetici hesapları yönetiyor ve "kim ne kadar kullandı" görüyor; "kim ne
 *  sordu" ya da "belgede ne yazıyor" görmüyor. Sunucu da öyle: bu panelin
 *  konuştuğu uçlar böyle bir veri döndürmüyor.
 */
import { useEffect, useState } from "react";
import { api } from "../api";
import type { Kullanici, KullanimSatiri } from "../types";
import { tarih } from "../sozluk";
import { Ikon } from "./Ogeler";

export function Yonetim({ ben, onKapat }: { ben: Kullanici; onKapat: () => void }) {
  const [kullanicilar, setKullanicilar] = useState<Kullanici[]>([]);
  const [kullanim, setKullanim] = useState<KullanimSatiri[]>([]);
  const [hata, setHata] = useState("");
  const [bilgi, setBilgi] = useState("");
  const [yeni, setYeni] = useState({ kullanici: "", ad: "", parola: "", rol: "user" });
  const [silinecek, setSilinecek] = useState<number | null>(null);
  const [parolaHedef, setParolaHedef] = useState<number | null>(null);
  const [yeniParola, setYeniParola] = useState("");

  async function yenile() {
    try {
      const [liste, istatistik] = await Promise.all([api.kullanicilar(), api.istatistik()]);
      setKullanicilar(liste);
      setKullanim(istatistik.kullanicilar);
    } catch (e) {
      setHata(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => { yenile(); }, []);

  async function calistir(is: Promise<unknown>, mesaj: string) {
    setHata("");
    setBilgi("");
    try {
      await is;
      setBilgi(mesaj);
      await yenile();
      return true;
    } catch (e) {
      setHata(e instanceof Error ? e.message : String(e));
      return false;
    }
  }

  const kullanimi = (id: number) => kullanim.find((k) => k.id === id);

  return (
    <div className="yonetim">
      <div className="yonetim-bas">
        <h2>Yönetim</h2>
        <button className="ikon-dugme" onClick={onKapat} title="Kapat" aria-label="Kapat">
          <Ikon ad="close" />
        </button>
      </div>

      <p className="kucuk-not">
        Hesaplar ve kullanım. Kullanıcıların belgeleri ve sohbetleri buradan
        görünmez — yalnızca sahipleri erişebilir.
      </p>

      {hata && <p className="hata-satiri" role="alert">{hata}</p>}
      {bilgi && <p className="kucuk-not" role="status">{bilgi}</p>}

      <h3 className="yonetim-baslik">Yeni hesap</h3>
      <form
        className="yeni-hesap"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!yeni.kullanici.trim() || !yeni.parola) return;
          const oldu = await calistir(
            api.kullaniciAc({ ...yeni, kullanici: yeni.kullanici.trim().toLowerCase() }),
            `${yeni.kullanici} eklendi`,
          );
          if (oldu) setYeni({ kullanici: "", ad: "", parola: "", rol: "user" });
        }}
      >
        <label className="alan">
          <span>Kullanıcı adı</span>
          <input value={yeni.kullanici} spellCheck={false}
                 onChange={(e) => setYeni({ ...yeni, kullanici: e.target.value })} />
        </label>
        <label className="alan">
          <span>Ad soyad</span>
          <input value={yeni.ad} onChange={(e) => setYeni({ ...yeni, ad: e.target.value })} />
        </label>
        <label className="alan">
          <span>Geçici parola</span>
          <input value={yeni.parola} onChange={(e) => setYeni({ ...yeni, parola: e.target.value })} />
        </label>
        <label className="alan">
          <span>Rol</span>
          <select value={yeni.rol} onChange={(e) => setYeni({ ...yeni, rol: e.target.value })}>
            <option value="user">Kullanıcı</option>
            <option value="admin">Yönetici</option>
          </select>
        </label>
        <button className="birincil" type="submit">
          <Ikon ad="person_add" /> Hesap aç
        </button>
      </form>

      <h3 className="yonetim-baslik">Hesaplar</h3>
      <div className="tablo-kap">
        <table className="yonetim-tablo">
          <thead>
            <tr>
              <th>Kullanıcı</th><th>Rol</th><th>Son giriş</th>
              <th>Belge</th><th>Sohbet</th><th>Sorgu</th><th>Maliyet</th><th></th>
            </tr>
          </thead>
          <tbody>
            {kullanicilar.map((k) => {
              const u = kullanimi(k.id);
              return (
                <tr key={k.id}>
                  <td>
                    <strong>{k.ad}</strong>
                    <span className="kullanici-adi">@{k.username}</span>
                  </td>
                  <td>
                    <select value={k.rol} disabled={k.id === ben.id}
                            onChange={(e) => calistir(api.rolDegistir(k.id, e.target.value),
                                                      `${k.username} rolü güncellendi`)}>
                      <option value="user">Kullanıcı</option>
                      <option value="admin">Yönetici</option>
                    </select>
                  </td>
                  <td>{k.last_login ? tarih(k.last_login) : "—"}</td>
                  <td>{u?.belge ?? 0}</td>
                  <td>{u?.sohbet ?? 0}</td>
                  <td>{u?.sorgu ?? 0}</td>
                  <td>${(u?.maliyet ?? 0).toFixed(4)}</td>
                  <td className="eylem-hucre">
                    <button className="ikon-dugme" title="Parola sıfırla"
                            aria-label={`${k.username} parolasını sıfırla`}
                            onClick={() => { setParolaHedef(k.id); setYeniParola(""); }}>
                      <Ikon ad="key" />
                    </button>
                    {k.id !== ben.id && (
                      <button className="ikon-dugme sil" title="Hesabı sil"
                              aria-label={`${k.username} hesabını sil`}
                              onClick={() => setSilinecek(k.id)}>
                        <Ikon ad="delete" />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {parolaHedef !== null && (
        <form className="satir-form" onSubmit={async (e) => {
          e.preventDefault();
          if (!yeniParola) return;
          const oldu = await calistir(api.parolaSifirla(parolaHedef, yeniParola),
                                      "Parola güncellendi, kullanıcının oturumları kapandı");
          if (oldu) { setParolaHedef(null); setYeniParola(""); }
        }}>
          <label className="alan">
            <span>
              {kullanicilar.find((k) => k.id === parolaHedef)?.username} için yeni parola
            </span>
            <input value={yeniParola} onChange={(e) => setYeniParola(e.target.value)} autoFocus />
          </label>
          <button className="birincil" type="submit">Kaydet</button>
          <button type="button" onClick={() => setParolaHedef(null)}>Vazgeç</button>
        </form>
      )}

      {silinecek !== null && (
        <div className="silme-onay">
          <p className="onay">
            “{kullanicilar.find((k) => k.id === silinecek)?.username}” hesabı, yüklediği
            belgeler ve sohbetleri silinsin mi? Geri alınamaz.
          </p>
          <div className="onay-dugmeler">
            <button className="evet" onClick={async () => {
              await calistir(api.kullaniciSil(silinecek), "Hesap silindi");
              setSilinecek(null);
            }}>Sil</button>
            <button onClick={() => setSilinecek(null)}>Vazgeç</button>
          </div>
        </div>
      )}
    </div>
  );
}
