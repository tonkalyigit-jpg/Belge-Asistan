/** Kaynak paneli — iddiayı doğrulamanın yolu.
 *
 *  Belge başına bir blok: adı, dosyası, kullanılan bölümlerin metni ve o
 *  bölümlerin geçtiği sayfaların GÖRÜNTÜSÜ. "Uydurmuyor" iddiasının en güçlü
 *  kanıtı chunk metni değil, kâğıdın kendisi.
 *
 *  Belgenin tamamı okunduğunda bölüm bölüm metin basmak (29 kutu) kaynağı
 *  göstermiyor, belgeyi yeniden yazıyor: o durumda yalnızca sayfa aralığı.
 */
import { useState } from "react";
import type { Sonuc } from "../types";
import { T, type Dil } from "../sozluk";
import { api } from "../api";
import { Ikon, Katlanir } from "./Ogeler";

export function Kaynaklar({ sonuc }: { sonuc: Sonuc }) {
  const atiflar = sonuc.citations ?? [];
  const [acikSayfa, setAcikSayfa] = useState<[number, number] | null>(null);
  if (!atiflar.length) return null;

  const dil: Dil = sonuc.lang === "en" ? "en" : "tr";
  const t = T[dil];
  const tam = Boolean(sonuc.full_context);
  const pasajSayisi = atiflar.reduce((n, a) => n + (a.passages?.length ?? 0), 0);

  let baslik = `${t.sources} — ${atiflar.length} ${t.document_word}`;
  if (tam) baslik += `, ${t.full_read}`;
  else if (pasajSayisi) baslik += `, ${pasajSayisi} ${t.passage_word}`;

  return (
    <Katlanir baslik={baslik} cocuk={
      <>
        {atiflar.map((atif) => {
          const sayfalar = [...new Set(
            (atif.passages ?? []).flatMap((p) => [p.page_start, p.page_end])
              .filter((s): s is number => typeof s === "number"),
          )].sort((a, b) => a - b);
          const meta = tam && sayfalar.length
            ? `${atif.filename} · ${t.full_read} · ${
                sayfalar[0] === sayfalar[sayfalar.length - 1]
                  ? `s. ${sayfalar[0]}`
                  : `s. ${sayfalar[0]}–${sayfalar[sayfalar.length - 1]}`
              }`
            : atif.filename;

          return (
            <div className="kaynak" key={atif.n}>
              <div>
                <span className="kaynak-no">[{atif.n}]</span>
                <span className="kaynak-ad">{atif.title}</span>
              </div>
              <span className="kaynak-meta">{meta}</span>

              {sayfalar.length > 0 && (
                <div className="sayfa-dugmeler">
                  {sayfalar.slice(0, 6).map((no) => {
                    const acik = acikSayfa?.[0] === atif.document_id && acikSayfa?.[1] === no;
                    return (
                      <button
                        key={no}
                        className={acik ? "acik" : ""}
                        title={t.show_page}
                        // İkon + "s. 3" görünür ad veriyor ama panel kapalıyken
                        // ekran okuyucuya hiçbir şey ulaşmıyor; açık ad şart.
                        aria-label={`${t.show_page} — ${t.page_word} ${no}`}
                        aria-expanded={acik}
                        onClick={() => setAcikSayfa(acik ? null : [atif.document_id, no])}
                      >
                        <Ikon ad="description" /> s. {no}
                      </button>
                    );
                  })}
                </div>
              )}

              {acikSayfa?.[0] === atif.document_id && sayfalar.includes(acikSayfa[1]) && (
                <>
                  <img
                    className="sayfa-gorsel"
                    src={api.sayfaUrl(atif.document_id, acikSayfa[1])}
                    alt={`${atif.title} — ${t.page_word} ${acikSayfa[1]}`}
                    loading="lazy"
                  />
                  <span className="sayfa-altyazi">{t.page_word} {acikSayfa[1]}</span>
                </>
              )}

              {!tam && (atif.passages ?? []).map((pasaj, n) =>
                pasaj.text?.trim() ? (
                  <div className="pasaj" key={n}>
                    {pasaj.section ? <span className="pasaj-bas">{pasaj.section}</span> : null}
                    {pasaj.text}{pasaj.truncated ? "…" : ""}
                  </div>
                ) : null,
              )}
            </div>
          );
        })}
      </>
    } />
  );
}
