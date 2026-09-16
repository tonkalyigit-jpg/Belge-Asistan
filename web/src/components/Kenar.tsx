/** Kenar çubuğu: belgeler, sohbetler, kullanım verileri.
 *
 *  Açık başlıyor, çünkü bu uygulamada birincil eylem belge yüklemek ve yükleme
 *  burada. Kapalı başlasaydı ilk ekran boş bir sohbet kutusu olurdu.
 */
import { useRef, useState } from "react";
import type { Belge, Durum, Kullanici, SohbetOzeti } from "../types";
import { T, tarih, tur } from "../sozluk";
import { Ikon, Katlanir } from "./Ogeler";

const t = T.tr;   // sayfa çatısı hep Türkçe

interface Props {
  belgeler: Belge[];
  sohbetler: SohbetOzeti[];
  acikSohbet: number | null;
  durum: Durum | null;
  odak: number | null;
  yukleniyor: string | null;
  onYukle: (dosyalar: FileList, ortak?: boolean) => void;
  onBelgeSil: (id: number) => void;
  onOdak: (id: number | null) => void;
  onSohbetAc: (id: number) => void;
  onSohbetSil: (id: number) => void;
  onYeniSohbet: () => void;
  acik: boolean;
  ben: Kullanici;
  onCikis: () => void;
  onYonetim: () => void;
}

export function Kenar(p: Props) {
  const [acikBelge, setAcikBelge] = useState<number | null>(null);
  const [silinecekBelge, setSilinecekBelge] = useState<number | null>(null);
  const [silinecekSohbet, setSilinecekSohbet] = useState<number | null>(null);
  const [uzerinde, setUzerinde] = useState(false);
  const [ortak, setOrtak] = useState(false);
  const girdi = useRef<HTMLInputElement>(null);
  const yonetici = p.ben.rol === "admin";

  return (
    <aside className={`kenar${p.acik ? " acik" : ""}`}>
      <div className="kenar-baslik">{t.documents}</div>

      <div
        className={`yukleyici${uzerinde ? " uzerinde" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setUzerinde(true); }}
        onDragLeave={() => setUzerinde(false)}
        onDrop={(e) => {
          e.preventDefault();
          setUzerinde(false);
          if (e.dataTransfer.files.length) p.onYukle(e.dataTransfer.files, ortak);
        }}
      >
        <input ref={girdi} type="file" accept="application/pdf" multiple hidden
               onChange={(e) => {
                 if (e.target.files?.length) p.onYukle(e.target.files, ortak);
                 e.target.value = "";     // aynı dosya tekrar seçilebilsin
               }} />
        <button className="secdugme" onClick={() => girdi.current?.click()}>
          <Ikon ad="upload" /> {t.upload}
        </button>
        <span className="ipucu">{p.yukleniyor ?? t.upload_hint}</span>
        {/* Ortak havuz YALNIZCA yöneticide: sıradan bir kullanıcı kendi
            belgesini herkese açamıyor (sunucu da reddediyor). */}
        {yonetici && (
          <label className="ortak-secim">
            <input type="checkbox" checked={ortak}
                   onChange={(e) => setOrtak(e.target.checked)} />
            <span>Ortak havuza yükle — herkes görür</span>
          </label>
        )}
      </div>

      {!p.belgeler.length && <p className="kucuk-not" style={{ marginTop: ".8rem" }}>{t.no_documents}</p>}

      <div style={{ marginTop: ".6rem" }}>
        {p.belgeler.map((belge) => {
          const ad = belge.title || belge.filename;
          const secili = acikBelge === belge.id;
          const odakli = p.odak === belge.id;
          const benim = belge.owner_id === p.ben.id;

          if (silinecekBelge === belge.id) {
            return (
              <div key={belge.id}>
                <div className="onay">“{ad}” silinsin mi?</div>
                <div className="onay-dugmeler">
                  <button className="evet" onClick={() => { p.onBelgeSil(belge.id); setSilinecekBelge(null); }}>
                    Sil
                  </button>
                  <button onClick={() => setSilinecekBelge(null)}>Vazgeç</button>
                </div>
              </div>
            );
          }

          return (
            <div key={belge.id}>
              <div className="satir">
                {/* <button> DEĞİL, role="button".
                    Chrome bir butonun İÇİNDEKİ her kutuyu "blockify" ediyor:
                    `-webkit-box` -> `flow-root` ve `-webkit-line-clamp` hiç
                    çalışmıyor. ÖLÇÜLDÜ: üç satırlık belge adında kutu 38px'te
                    kalıyor ama metin 57px; üçüncü satır "…" olmadan yarım
                    kesilip hayalet bir satır gibi görünüyordu. Klavye davranışı
                    elle veriliyor: Enter ve Space. */}
                <div className={`satir-ad${odakli ? " secili" : ""}`}
                     role="button" tabIndex={0} aria-expanded={secili} title={ad}
                     onClick={() => setAcikBelge(secili ? null : belge.id)}
                     onKeyDown={(e) => {
                       if (e.key === "Enter" || e.key === " ") {
                         e.preventDefault();
                         setAcikBelge(secili ? null : belge.id);
                       }
                     }}>
                  <span className="ad">
                      {odakli ? "◉ " : ""}
                    {belge.status === "processing" ? "… " : belge.status === "failed" ? "! " : ""}
                    {ad}
                    {belge.paylasim === "ortak" && <span className="ortak-rozet">ortak</span>}
                  </span>
                </div>
                {/* Silme yalnızca SAHİBİNDE: ortak havuzdaki bir belgeyi
                    kullanan kişi silemez, yönetici de başkasınınkini silmez. */}
                {benim && (
                  <button className="satir-sil" title={t.delete_doc} aria-label={t.delete_doc}
                          onClick={() => setSilinecekBelge(belge.id)}>
                    <Ikon ad="delete" />
                  </button>
                )}
              </div>

              {secili && (
                <div className="belge-bilgi">
                  <dl>
                    <div><dt>{t.doc_uploaded}</dt><dd>{tarih(belge.added_at)}</dd></div>
                    {tur(belge.summary) && (
                      <div><dt>{t.doc_type}</dt><dd>{tur(belge.summary)}</dd></div>
                    )}
                    <div>
                      <dt>{t.doc_pages}</dt>
                      <dd>{belge.page_count}{belge.ocr_pages ? ` · ${belge.ocr_pages} ${t.ocr_pages}` : ""}</dd>
                    </div>
                    <div><dt>{t.doc_file}</dt><dd>{belge.filename}</dd></div>
                  </dl>
                  {belge.status === "failed" ? (
                    <p className="kucuk-not">{belge.error || "işlenemedi"}</p>
                  ) : (
                    <button className="odak-dugme"
                            onClick={() => {
                              // Odak sohbete çekiliyor: sonraki sorular yalnızca bu
                              // belgede aranıyor. Kart kapanıyor — işaret artık soru
                              // kutusunun üstünde.
                              p.onOdak(odakli ? null : belge.id);
                              setAcikBelge(null);
                            }}>
                      <Ikon ad={odakli ? "close" : "chat"} />
                      {odakli ? t.focus_clear : t.focus_ask}
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="ayrac" />

      <button className="yeni-sohbet" onClick={p.onYeniSohbet}>
        <Ikon ad="add" /> Yeni sohbet
      </button>

      {p.sohbetler.length > 0 && (
        <>
          <div className="kenar-baslik" style={{ margin: "1rem 0 .5rem" }}>Sohbetler</div>
          {p.sohbetler.map((sohbet) => {
            if (silinecekSohbet === sohbet.id) {
              return (
                <div key={sohbet.id}>
                  <div className="onay">“{sohbet.title}” silinsin mi?</div>
                  <div className="onay-dugmeler">
                    <button className="evet" onClick={() => { p.onSohbetSil(sohbet.id); setSilinecekSohbet(null); }}>
                      Sil
                    </button>
                    <button onClick={() => setSilinecekSohbet(null)}>Vazgeç</button>
                  </div>
                </div>
              );
            }
            return (
              <div className="satir" key={sohbet.id}>
                <div className={`satir-ad${sohbet.id === p.acikSohbet ? " secili" : ""}`}
                     role="button" tabIndex={0} title={sohbet.title}
                     aria-current={sohbet.id === p.acikSohbet ? "true" : undefined}
                     onClick={() => p.onSohbetAc(sohbet.id)}
                     onKeyDown={(e) => {
                       if (e.key === "Enter" || e.key === " ") {
                         e.preventDefault();
                         p.onSohbetAc(sohbet.id);
                       }
                     }}>
                  <span className="ad">
                    {sohbet.id === p.acikSohbet ? "▸ " : ""}{sohbet.title}
                  </span>
                </div>
                <button className="satir-sil" title="Sohbeti sil" aria-label="Sohbeti sil"
                        onClick={() => setSilinecekSohbet(sohbet.id)}>
                  <Ikon ad="delete" />
                </button>
              </div>
            );
          })}
        </>
      )}

      <div className="ayrac" />

      {/* Kullanım verileri KATLANMIŞ: sistemi geliştirirken gerekli, soru soran
          kullanıcı için gürültü. */}
      <Katlanir sinif="katlanir" baslik={t.cost} cocuk={
        <>
          <dl className="olcum">
            <div><dt>{t.queries}</dt><dd>{p.durum?.maliyet.queries ?? 0}</dd></div>
            <div><dt>{t.avg}</dt><dd>${(p.durum?.maliyet.avg_cost ?? 0).toFixed(4)}</dd></div>
          </dl>
          <div className="kucuk-not">toplam ${(p.durum?.maliyet.total_cost ?? 0).toFixed(4)}</div>
          <div className="kucuk-not">
            {Object.entries(p.durum?.maliyet.by_route ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ")}
          </div>
        </>
      } />
      <Katlanir sinif="katlanir" baslik={t.models} cocuk={
        <>
          {Object.entries(p.durum?.modeller ?? {}).map(([ad, bilgi]) => (
            <div className="model-kart" key={ad}>
              <b>{ad}</b><br />
              {bilgi.model.split("/").pop()}<br />
              <span>{bilgi.provider} · ${bilgi.price_in} / ${bilgi.price_out} per 1M</span>
            </div>
          ))}
        </>
      } />

      <div className="ayrac" />

      {/* Kim olduğun her zaman görünür: paylaşılan bir makinede "hangi hesapla
          açığım" sorusu belge yüklemeden önce cevaplanmalı. */}
      <div className="hesap">
        <div className="hesap-ad">
          <Ikon ad="account_circle" />
          <span>
            <strong>{p.ben.ad}</strong>
            <span className="hesap-rol">{yonetici ? "yönetici" : "kullanıcı"}</span>
          </span>
        </div>
        {yonetici && (
          <button className="hesap-dugme" onClick={p.onYonetim}>
            <Ikon ad="settings" /> Yönetim
          </button>
        )}
        <button className="hesap-dugme" onClick={p.onCikis}>
          <Ikon ad="logout" /> Çıkış
        </button>
      </div>
    </aside>
  );
}
