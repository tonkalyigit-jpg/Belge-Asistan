/** Paylaşılan küçük parçalar: ikon, markdown, katlanır panel. */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import type { CSSProperties, ReactNode } from "react";

/** Material Symbols ligatürü. Emoji kullanılmıyor: her platformda farklı
 *  çiziliyor, renkli geliyor ve çizgi kalınlığı arayüzle uyuşmuyor. */
export function Ikon({ ad, dolu = false, className = "", style }:
  { ad: string; dolu?: boolean; className?: string; style?: CSSProperties }) {
  return (
    <span className={`ikon${dolu ? " dolu" : ""} ${className}`} style={style} aria-hidden="true">
      {ad}
    </span>
  );
}

/** Cevap metni. Matematik KaTeX ile, tablolar kendi kaydırılabilir kabında.
 *  Tablo kabı ŞART: üç belgeli bir karşılaştırma tablosu sohbet sütunundan
 *  geniş olabiliyor ve sayfanın tamamı yatay kaymamalı. */
export function Markdown({ metin }: { metin: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, {
          // Model bazen bozuk LaTeX yazıyor ("\\hat{a}__\\phi"). Varsayılan
          // davranış o parçayı KIRMIZI basıyor ve cevabın ortasında hata gibi
          // duruyor; oysa okunabilir bir metin parçası. Kırmızı yerine sönük
          // renk: yanlış olduğu belli ama cevabı basmıyor.
          throwOnError: false,
          errorColor: "#8C9099",
        }]]}
        components={{
          table: ({ children }) => (
            <div className="tablo-kap">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {metin}
      </ReactMarkdown>
    </div>
  );
}

/** Katlanır panel — kaynaklar, iz, maliyet. `<details>` üzerine kuruldu:
 *  klavye ve ekran okuyucu davranışı tarayıcıdan geliyor. */
export function Katlanir({ baslik, ikon, sinif = "panel", cocuk, acikBaslangic = false }:
  { baslik: ReactNode; ikon?: string; sinif?: string; cocuk: ReactNode; acikBaslangic?: boolean }) {
  return (
    <details className={sinif} open={acikBaslangic}>
      <summary>
        <Ikon ad="chevron_right" className="ok" />
        {ikon ? <Ikon ad={ikon} /> : null}
        <span>{baslik}</span>
      </summary>
      <div className={sinif === "panel" ? "panel-icerik" : "katlanir-icerik"}>{cocuk}</div>
    </details>
  );
}
