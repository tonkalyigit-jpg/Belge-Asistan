/** Belge Asistanı — React arayüzü.
 *
 *  Streamlit sürümüyle aynı çekirdeği (api/server.py üzerinden) ve aynı tasarım
 *  dilini kullanıyor. Akışın her kararı (kategori, yol, model, maliyet, hangi
 *  bölümler okundu) görünür: "yalnızca belgeden cevap veriyor" iddiası ancak
 *  nereden cevap verdiği ekrandayken savunulabilir.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, sor, yukle } from "./api";
import type { Belge, Durum, Kullanici, Mesaj, SohbetOzeti, Sonuc } from "./types";
import { ADIM, T, YUKLEME_ASAMA } from "./sozluk";
import { Alt } from "./components/Alt";
import { Giris } from "./components/Giris";
import { Hesap } from "./components/Hesap";
import { Yonetim } from "./components/Yonetim";
import { Eylemler } from "./components/Eylemler";
import { Iz } from "./components/Iz";
import { Kaynaklar } from "./components/Kaynaklar";
import { Kenar } from "./components/Kenar";
import { Ikon, Markdown } from "./components/Ogeler";
import { Rozetler } from "./components/Rozetler";

const t = T.tr;

const ORNEKLER = [
  "Bu sözleşmede gecikme cezası ne kadar?",
  "Belgeleri özetle",
  "Hangi sözleşme yönetmeliğe aykırı?",
  "Teslim süreleri sözleşmelerde nasıl farklılaşıyor?",
];

export default function App() {
  const [belgeler, setBelgeler] = useState<Belge[]>([]);
  const [sohbetler, setSohbetler] = useState<SohbetOzeti[]>([]);
  const [sohbetId, setSohbetId] = useState<number | null>(null);
  const [mesajlar, setMesajlar] = useState<Mesaj[]>([]);
  const [durum, setDurum] = useState<Durum | null>(null);
  const [odak, setOdak] = useState<number | null>(null);
  const [kip, setKip] = useState("writer");
  const [yuklemeNotu, setYuklemeNotu] = useState<string | null>(null);
  const [kenarAcik, setKenarAcik] = useState(false);
  const [hata, setHata] = useState<string | null>(null);
  // `undefined` = oturum henüz sorulmadı, `null` = giriş gerekiyor.
  const [ben, setBen] = useState<Kullanici | null | undefined>(undefined);
  const [yonetimAcik, setYonetimAcik] = useState(false);

  // Akış durumu: biten adımlar, çalışan adım, o ana kadar yazılan metin.
  const [calisiyor, setCalisiyor] = useState(false);
  const [adimlar, setAdimlar] = useState<string[]>([]);
  const [canliAdim, setCanliAdim] = useState<string | null>(null);
  const [akanMetin, setAkanMetin] = useState("");
  const [gecenSure, setGecenSure] = useState(0);
  const akis = useRef<HTMLDivElement>(null);
  // Kullanıcı yukarı kaydırıp eski mesajları okuyorsa akan metin onu aşağı
  // ÇEKMEMELİ. Her akış turunda dibe yakın olup olmadığına bakılıyor.
  const dibeYakin = useRef(true);

  const yenile = useCallback(async () => {
    const [b, s, d] = await Promise.all([api.belgeler(), api.sohbetler(), api.durum()]);
    setBelgeler(b);
    setSohbetler(s);
    setDurum(d);
    return s;
  }, []);

  const acSohbet = useCallback(async (id: number) => {
    dibeYakin.current = true;
    setSohbetId(id);
    setOdak(null);      // odak sohbete ait bir karar, başka sohbete taşınmamalı
    setKenarAcik(false);
    try {
      const veri = await api.sohbet(id);
      setMesajlar(veri.mesajlar);
    } catch (e) {
      // Sohbet başka bir sekmede silinmiş olabilir: boş aç ve söyle.
      setMesajlar([]);
      setHata(`Sohbet açılamadı: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, []);

  // Açılışta önce KİMLİK: oturum yoksa giriş ekranı çiziliyor ve hiçbir veri
  // isteği yapılmıyor (hepsi 401 dönerdi).
  useEffect(() => {
    api.ben().then(setBen).catch(() => setBen(null));
  }, []);

  useEffect(() => {
    if (!ben) return;
    yenile()
      .then((s) => { if (s.length) acSohbet(s[0].id); else setMesajlar([]); })
      .catch((e) => setHata(String(e)));
  }, [ben, yenile, acSohbet]);

  // Süre sayacı yalnızca çalışırken dönüyor: boştayken saniyede bir render
  // etmenin anlamı yok.
  useEffect(() => {
    if (!calisiyor) return;
    const basladi = Date.now();
    const sayac = setInterval(() => setGecenSure((Date.now() - basladi) / 1000), 500);
    return () => clearInterval(sayac);
  }, [calisiyor]);

  // Yeni mesaj ya da akan metin geldiğinde dibe in: cevap üretimi 5-80 saniye
  // sürüyor ve kullanıcı o süre boyunca kendi sorusunu göremiyordu. Sayfanın
  // kendisi değil SOHBET ALANI kaydırılıyor; soru kutusu akışın dışında.
  useEffect(() => {
    const el = akis.current;
    if (!el || !dibeYakin.current) return;
    el.scrollTop = el.scrollHeight;
  }, [mesajlar, akanMetin, calisiyor, canliAdim]);

  function kaydirmaIzle() {
    const el = akis.current;
    if (!el) return;
    dibeYakin.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }

  /** API çağrısını sarar: hata sessizce yutulmasın, ekranda görünsün.
   *  Sunucu düşerse ya da bir istek reddedilirse arayüz "hiçbir şey olmadı"
   *  gibi davranıyordu; kullanıcı tıklayıp bekliyordu. */
  async function guvenle<T>(is: Promise<T>, nerede: string): Promise<T | null> {
    try {
      return await is;
    } catch (e) {
      setHata(`${nerede}: ${e instanceof Error ? e.message : String(e)}`);
      return null;
    }
  }

  async function yeniSohbet() {
    setOdak(null);
    if (!mesajlar.length && sohbetId) return;   // boş sohbet varken yenisi gereksiz
    const yeni = await guvenle(api.sohbetAc(), "Sohbet açılamadı");
    if (!yeni) return;
    setSohbetId(yeni.id);
    setMesajlar([]);
    const liste = await guvenle(api.sohbetler(), "Sohbet listesi alınamadı");
    if (liste) setSohbetler(liste);
  }

  async function dosyaYukle(dosyalar: FileList, ortak = false) {
    for (const dosya of Array.from(dosyalar)) {
      setYuklemeNotu(`${dosya.name} okunuyor…`);
      await new Promise<void>((bitir) => {
        yukle(dosya, ortak, {
          onAsama: (asama, i, n) =>
            setYuklemeNotu(`${dosya.name} — ${YUKLEME_ASAMA[asama] ?? asama}${n > 1 ? ` ${i}/${n}` : ""}`),
          onBitti: (sonuc) => {
            if (sonuc.durum === "failed") {
              setYuklemeNotu(`${dosya.name} — okunamadı: ${sonuc.hata}`);
            } else if (sonuc.durum === "duplicate") {
              setYuklemeNotu(`${sonuc.baslik} — zaten yüklü`);
            } else {
              setYuklemeNotu(null);
              // Özet sohbete asistan mesajı olarak düşüyor: kullanıcı belgeyi
              // attığında "okudum, içinde şunlar var" cevabını görmeli.
              const ocrNotu = sonuc.ocr_sayfa ? `, ${sonuc.ocr_sayfa} ${t.ocr_pages}` : "";
              setMesajlar((eski) => [...eski, {
                role: "assistant",
                content: `**${sonuc.baslik}** ${t.uploaded} — ${sonuc.sayfa} ${t.pages}${ocrNotu}.\n\n${sonuc.ozet}`,
                result: {
                  kind: "yukleme", lang: "tr", category: "belge_ozeti", route: "ozet",
                  difficulty: 1, model_used: null, tier_used: null, cost_usd: 0,
                  total_ms: sonuc.saniye * 1000, rewrites: 0, regens: 0,
                  low_confidence: false, page_count: sonuc.sayfa, ocr_pages: sonuc.ocr_sayfa,
                  chunks: sonuc.chunk, warnings: sonuc.uyarilar,
                } as Sonuc,
              }]);
            }
            bitir();
          },
          onHata: (mesaj) => { setYuklemeNotu(`${dosya.name} — ${mesaj}`); bitir(); },
        }).catch(() => bitir());
      });
      await yenile();
    }
  }

  async function cikis() {
    await guvenle(api.cikis(), "Çıkış yapılamadı");
    setBen(null);
    setMesajlar([]);
    setSohbetler([]);
    setBelgeler([]);
    setSohbetId(null);
    setYonetimAcik(false);
  }

  async function belgeSil(id: number) {
    if (!(await guvenle(api.belgeSil(id), "Belge silinemedi"))) return;
    if (odak === id) setOdak(null);
    await guvenle(yenile(), "Liste yenilenemedi");
  }

  async function sohbetSil(id: number) {
    if (!(await guvenle(api.sohbetSil(id), "Sohbet silinemedi"))) return;
    const kalan = (await guvenle(api.sohbetler(), "Sohbet listesi alınamadı")) ?? [];
    setSohbetler(kalan);
    if (id === sohbetId) {
      if (kalan.length) await acSohbet(kalan[0].id);
      else { setSohbetId(null); setMesajlar([]); }
    }
  }

  async function calistir(soru: string, yeniden = false) {
    setHata(null);
    setCalisiyor(true);
    setAdimlar([]);
    setCanliAdim(null);
    setAkanMetin("");
    setGecenSure(0);
    if (!yeniden) setMesajlar((eski) => [...eski, { role: "user", content: soru }]);

    try {
      await sor(
        { soru, sohbet_id: sohbetId, kip, odak_belge_id: odak, yeniden },
        {
          onSohbet: (id) => setSohbetId(id),
          onAdim: (ad) => {
            setCanliAdim((onceki) => {
              if (onceki) setAdimlar((liste) => [...liste, onceki]);
              return ad;
            });
          },
          onParca: (parca) => setAkanMetin((eski) => eski + parca),
          onSon: (cevap, sonuc) => {
            // Akışta gösterilen taslak doğrulama düğümlerinden GEÇMEMİŞ hâliydi;
            // her durumda kesinleşmiş cevapla değiştiriliyor.
            setMesajlar((eski) => {
              const govde = yeniden ? eski.slice(0, -1) : eski;
              return [...govde, { role: "assistant", content: cevap, result: sonuc }];
            });
            setAkanMetin("");
          },
          onHata: (mesaj) => setHata(mesaj),
        },
      );
    } catch (e) {
      setHata(String(e));
    } finally {
      setCalisiyor(false);
      setCanliAdim(null);
      setAdimlar([]);
      api.sohbetler().then(setSohbetler).catch(() => undefined);
      api.durum().then(setDurum).catch(() => undefined);
    }
  }

  async function oyVer(sira: number, yon: 1 | -1, etiket: string) {
    const mesaj = mesajlar[sira];
    const sonuc = (mesaj.result ?? {}) as Sonuc;
    await guvenle(api.oy({
      soru: sonuc.query ?? "", yon, dil: sonuc.lang ?? "tr",
      kategori: sonuc.category, sohbet_id: sohbetId, sira, etiket,
    }), "Oy kaydedilemedi");
    setMesajlar((eski) => eski.map((m, i) => (i === sira ? { ...m, voted: etiket } : m)));
  }

  const odakBelge = belgeler.find((b) => b.id === odak && b.status === "ready") ?? null;
  const hazir = belgeler.filter((b) => b.status === "ready");

  // Oturum sorusu dönmeden hiçbir şey çizilmiyor: bir an sohbet ekranını
  // gösterip sonra giriş ekranına atlamak, "sistem beni attı" gibi görünüyor.
  if (ben === undefined) return <div className="yukleniyor" />;
  if (ben === null) return <Giris onGiris={(k) => { setBen(k); setHata(null); }} />;

  return (
    <div className="kabuk">
      <button className="kenar-anahtar" onClick={() => setKenarAcik((a) => !a)}
              aria-label="Kenar çubuğu" aria-expanded={kenarAcik}>
        <Ikon ad={kenarAcik ? "close" : "menu"} />
      </button>
      {kenarAcik && <div className="perde" onClick={() => setKenarAcik(false)} />}

      <Kenar
          belgeler={belgeler}
          sohbetler={sohbetler}
          acikSohbet={sohbetId}
          durum={durum}
          odak={odak}
          yukleniyor={yuklemeNotu}
          onYukle={dosyaYukle}
          onBelgeSil={belgeSil}
          onOdak={setOdak}
          onSohbetAc={acSohbet}
          onSohbetSil={sohbetSil}
        onYeniSohbet={yeniSohbet}
        acik={kenarAcik}
        ben={ben}
        bosSohbet={!mesajlar.length && !calisiyor}
      />

      <main className="govde">
        <Hesap ben={ben} onCikis={cikis}
               onAyarlar={() => { setYonetimAcik(true); setKenarAcik(false); }} />
        {yonetimAcik && ben.rol === "admin" ? (
          <div className="akis">
            <div className="sutun">
              <Yonetim ben={ben} onKapat={() => setYonetimAcik(false)} />
            </div>
          </div>
        ) : (
        <>
        <div className="akis" ref={akis} onScroll={kaydirmaIzle}>
        <div className="sutun" role="log" aria-live="polite" aria-busy={calisiyor}>
          <div className="manset">
            <h1>
              <img src="/orion-logo.png" alt="Orion Innovation" />
              {t.title}
            </h1>
            <p>{t.subtitle}</p>
          </div>

          {!mesajlar.length && !calisiyor && (
            <>
              {durum && !durum.isinma.bitti && (
                <p className="kucuk-not">
                  Arama motoru hazırlanıyor ({durum.isinma.saniye.toFixed(0)} sn) —
                  şimdi belge yükleyebilir ya da soru yazabilirsiniz
                </p>
              )}
              <div className="bos">
                <h4>Ne sorabilirsiniz</h4>
                <p>
                  {hazir.length
                    ? `${hazir.length} belge yüklü. Sorunuz bu belgelerin içinde aranır; cevap yalnızca bulunan bölümlere dayanır ve sayfa numarasıyla verilir.`
                    : "Başlamak için sol panelden bir PDF yükleyin. Dijital PDF'ler ve taranmış belgeler okunur; yükleme bitince belgenin özetini çıkarırım."}
                </p>
                <div>
                  {ORNEKLER.map((ornek) => (
                    <button className="cips" key={ornek} onClick={() => calistir(ornek)}>
                      <b>›</b> {ornek}
                    </button>
                  ))}
                </div>
                <div className="bos-not">
                  Belgede olmayan bir şeyi sorarsanız sistem bunu söyler, uydurmaz.
                </div>
              </div>
            </>
          )}

          {mesajlar.map((mesaj, sira) =>
            mesaj.role === "user" ? (
              <div className="mesaj kullanici" key={sira}>
                <img className="avatar" src="/avatar-user.png" alt="" />
                <div className="mesaj-govde"><p>{mesaj.content}</p></div>
              </div>
            ) : (
              <div className="mesaj" key={sira}>
                <img className="avatar" src="/avatar-assistant.png" alt="" />
                <div className="mesaj-govde">
                  {mesaj.result && <Rozetler sonuc={mesaj.result} />}
                  <Markdown metin={mesaj.content || t.no_answer} />
                  {mesaj.result?.kind === "yukleme" ? (
                    (mesaj.result.warnings ?? []).map((uyari, n) => (
                      <p className="kucuk-not" key={n}>{uyari}</p>
                    ))
                  ) : (
                    <>
                      {mesaj.result && <Kaynaklar sonuc={mesaj.result} />}
                      {mesaj.result && <Iz sonuc={mesaj.result} />}
                      <Eylemler
                        mesaj={mesaj}
                        sonMu={sira === mesajlar.length - 1}
                        onOy={(yon, etiket) => oyVer(sira, yon, etiket)}
                        onYeniden={() => {
                          const soru = [...mesajlar].slice(0, sira).reverse()
                            .find((m) => m.role === "user")?.content;
                          if (soru) calistir(soru, true);
                        }}
                      />
                    </>
                  )}
                </div>
              </div>
            ),
          )}

          {/* Çalışırken: önce adım listesi, metin akmaya başlayınca yerini
              cevaba bırakıyor — cevabın kendisi en iyi ilerleme göstergesi. */}
          {calisiyor && (
            <div className="mesaj">
              <img className="avatar" src="/avatar-assistant.png" alt="" />
              <div className="mesaj-govde" aria-live="polite" aria-atomic="false">
                {akanMetin ? (
                  <>
                    <Markdown metin={akanMetin} />
                    <span className="imlec">▌</span>
                  </>
                ) : (
                  <>
                    {adimlar.map((ad, n) => (
                      <div className="padim bitti" key={n}>✓ {(ADIM[ad] ?? [ad, ""])[0]}</div>
                    ))}
                    {canliAdim && (
                      <div className="padim canli">
                        <span className="pnokta" />
                        {(ADIM[canliAdim] ?? [canliAdim, ""])[0]}
                        <span className="pneden">{(ADIM[canliAdim] ?? ["", ""])[1]}</span>
                      </div>
                    )}
                    <div className="psure">{gecenSure.toFixed(0)} sn</div>
                  </>
                )}
              </div>
            </div>
          )}

          {hata && <p className="kucuk-not hata-satiri" role="alert">{hata}</p>}
          <div style={{ height: "1.5rem" }} />
        </div>
        </div>

        <Alt
          kip={kip}
          setKip={setKip}
          odakAdi={odakBelge ? odakBelge.title || odakBelge.filename : null}
          onOdakKaldir={() => setOdak(null)}
          calisiyor={calisiyor}
          onGonder={(soru) => calistir(soru)}
          modelAdlari={durum?.kipler ?? {}}
        />
        </>
        )}
      </main>
    </div>
  );
}
