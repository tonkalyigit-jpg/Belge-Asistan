"""Belge Asistanı — Streamlit arayüzü.

Kullanıcı PDF yüklüyor, sistem okuyup özetliyor ve sorulara yalnızca belgelere
dayanarak, sayfa numarasıyla cevap veriyor. Akışın her kararı (kategori, yol,
model, maliyet) görünür: "yalnızca belgeden cevap veriyor" iddiası ancak
nereden cevap verdiği ekrandayken savunulabilir.

Tasarım sistemi, akış gösterimi, iz paneli ve oylama Network Asistanı'ndan
taşındı; oradaki yorumlar orada yaşanmış hataların kaydı.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import html
import queue
import threading
import time

import streamlit as st
import streamlit.components.v1 as components

import config
from core import graph
from core.state import Category, Route
from llm import registry
from memory import conversations, feedback
from observability import trace
from belge import ingest
from belge.store import get_store

# Sekme simgesi: markanın kutulu hâli. Başlıktaki logo saydam ama tarayıcı
# sekmesi açık temada gri oluyor ve saydam beyaz işaret orada kaybolurdu.
_FAVICON = Path(__file__).parent / "assets" / "orion-favicon.png"

st.set_page_config(
    page_title="Belge Asistanı",
    page_icon=str(_FAVICON) if _FAVICON.exists() else "◈",
    layout="centered",
    # Açık başlıyor: bu uygulamada birincil eylem belge yüklemek ve yükleme
    # kenar çubuğunda. Kapalı başlasaydı ilk ekran boş bir sohbet kutusu olurdu.
    initial_sidebar_state="expanded",
)

# --- sözlükler -------------------------------------------------------------

T = {
    "tr": {
        "helpful_up": 'Bu yanıt işime yaradı',
        "helpful_down": 'Bu yanıt işime yaramadı',
        "regen": 'Yeniden üret',
        "copy": 'Cevabı kopyala',
        "copied": 'Kopyalandı.',
        "copy_manual": 'Panoya erişilemedi — metni aşağıdan kopyalayabilirsiniz.',
        "thanks_up": 'Teşekkürler — geri bildirim kaydedildi.',
        "thanks_down": 'Kaydedildi — sınıflandırıcı benzer sorularda bu örneği görecek.',
        "low_conf": 'doğrulanamadı',
        "no_answer": 'Yanıt üretilemedi.',
        "rate_limited": 'kota beklemesi',
        "difficulty": 'zorluk',
        "rewrites": 'yeniden yazma',
        "regens": 'yeniden üretim',
        "trace": 'Bu cevap nasıl üretildi',
        "trace_steps": 'adım',
        "title": 'Belge Asistanı',
        "subtitle": 'yüklediğiniz belgelerden, sayfa numarasıyla cevap',
        "placeholder": 'Belgeleriniz hakkında bir soru sorun…',
        "sources": 'Kaynaklar',
        "document_word": 'belge',
        "passage_word": 'bölüm',
        "full_read": 'tamamı okundu',
        "show_page": 'Belgedeki sayfayı göster',
        "page_word": 'Sayfa',
        "page_missing": 'Sayfa görüntüsü açılamadı.',
        "documents": 'Belgeler',
        "upload": 'PDF yükle',
        "pages": 'sayfa',
        "ocr_pages": 'taramadan okundu',
        "uploaded": 'yüklendi',
        "no_documents": 'Henüz belge yok. Bir PDF sürükleyin — dijital ya da taranmış.',
        "delete_doc": 'Belgeyi sil',
        "cost": 'Maliyet',
        "queries": 'sorgu',
        "avg": 'ortalama',
        "models": 'Modeller',
        "focus_ask": 'Bu belgeye sor',
        "focus_clear": 'Odağı kaldır',
        "focus_only": 'Yalnızca',
        "focus_badge": 'yalnızca',
        "placeholder_focus": '{title} hakkında sorun…',
        "doc_uploaded": 'Yüklendi',
        "doc_type": 'Tür',
        "doc_pages": 'Sayfa',
        "doc_file": 'Dosya',
    },
    "en": {
        "helpful_up": 'This answer was useful',
        "helpful_down": 'This answer was not useful',
        "regen": 'Regenerate',
        "copy": 'Copy answer',
        "copied": 'Copied.',
        "copy_manual": 'Clipboard unavailable — copy the text below.',
        "thanks_up": 'Thanks — feedback recorded.',
        "thanks_down": 'Recorded — the classifier will see this example for similar questions.',
        "low_conf": 'unverified',
        "no_answer": 'No answer could be generated.',
        "rate_limited": 'quota wait',
        "difficulty": 'difficulty',
        "rewrites": 'rewrites',
        "regens": 'regenerations',
        "trace": 'How this answer was produced',
        "trace_steps": 'steps',
        "title": 'Document Assistant',
        "subtitle": 'answers from your documents, with page references',
        "placeholder": 'Ask a question about your documents…',
        "sources": 'Sources',
        "document_word": 'documents',
        "passage_word": 'passages',
        "full_read": 'read in full',
        "show_page": 'Show the page in the document',
        "page_word": 'Page',
        "page_missing": 'The page image could not be opened.',
        "documents": 'Documents',
        "upload": 'Upload PDF',
        "pages": 'pages',
        "ocr_pages": 'read from scan',
        "uploaded": 'uploaded',
        "no_documents": 'No documents yet. Drop a PDF — digital or scanned.',
        "delete_doc": 'Delete document',
        "cost": 'Cost',
        "queries": 'queries',
        "avg": 'average',
        "models": 'Model tiers',
        "focus_ask": 'Ask this document',
        "focus_clear": 'Remove focus',
        "focus_only": 'Only',
        "focus_badge": 'only',
        "placeholder_focus": 'Ask about {title}…',
        "doc_uploaded": 'Uploaded',
        "doc_type": 'Type',
        "doc_pages": 'Pages',
        "doc_file": 'File',
    },
}

CATEGORY_LABEL = {
    Category.BELGE_ICI: ("Belge içi", "In document"),
    Category.CAPRAZ_BELGE: ("Çapraz belge", "Cross-document"),
    Category.BELGE_OZETI: ("Belge özeti", "Document summary"),
    Category.KAPSAM_DISI: ("Kapsam dışı", "Out of scope"),
}

ROUTE_LABEL = {
    Route.REFUSAL: ("Kibar ret", "Refusal"),
    Route.OZET: ("Saklı özet", "Stored summary"),
    Route.RAG: ("Belgelerden", "From documents"),
}

st.markdown(
    """
    <style>
      /* ---------------------------------------------------------------
         Tasarım yönü: BİLİMSEL ENSTRÜMAN + EDİTORYAL DERGİ
         Mürekkep zemin, kâğıt kremi metin, tek sinyal rengi (bakır).
         Üç tipografik kayıt ve her birinin bir işi var:
           Fraunces   — başlık; optik boyutlu, karakterli serif
           Newsreader — cevap gövdesi; uzun okuma için tasarlanmış serif
           IBM Plex Mono — telemetri; rozet, iz, sayı, model adı
         Ayrım kasıtlı: kullanıcı, MODELİN YAZDIĞI metinle SİSTEMİN ÖLÇTÜĞÜ
         veriyi bir bakışta ayırabilmeli.
      --------------------------------------------------------------- */
      @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600&family=Newsreader:opsz,wght@6..72,400;6..72,500&family=IBM+Plex+Mono:wght@400;500&display=swap');

      :root {
        --ink:      #0C0E11;
        --panel:    #14171C;
        --panel-2:  #171B21;
        --line:     #242A33;
        --paper:    #E6E1D7;
        --muted:    #8C9099;
        --signal:   #E9A85C;
        --signal-d: #8A5F28;
        --ok:       #7FB08A;
        --warn:     #D97A6C;
        --serif:    'Newsreader', Georgia, serif;
        --display:  'Fraunces', Georgia, serif;
        --mono:     'IBM Plex Mono', ui-monospace, monospace;
      }

      /* Streamlit'in üst boşluğu başlığı sayfadan koparıyordu */
      .block-container { padding-top: 2.2rem; max-width: 46rem; }

      /* --- SAYFA TİTREMESİ ---------------------------------------------
         İki ayrı mekanizma sayfayı sallıyordu, ikisi de aşağı kaydırınca
         belirginleşiyor:

         1) KAYDIRMA ÇUBUĞU SALINIMI. İçerik yüksekliği ekran yüksekliğinin
            tam etrafında dolaştığında dikey çubuk bir görünüp bir kayboluyor.
            Her görünüşünde sayfa genişliği ~15px değişiyor, metin yeniden
            akıyor, yükseklik yine değişiyor — kendini besleyen bir döngü.
            `scrollbar-gutter: stable` çubuğun yerini her zaman ayırıyor.

         2) KAYDIRMA ÇAPALAMA. Tarayıcı, içerik büyürken görünümü sabit
            tutmak için kaydırma konumunu kendiliğinden düzeltiyor. Akış
            sırasında metin saniyede 15 kez büyüdüğü için bu düzeltme
            sürekli tetikleniyor ve titreme olarak görünüyor. Sohbet
            akışında istediğimiz davranış zaten "aşağıda kal", çapalama
            değil.
      ------------------------------------------------------------------ */
      html { scrollbar-gutter: stable; }

      /* Mobil tarayıcılar çift dokunma yakınlaştırmasını beklemek için her
         dokunuşa ~300 ms gecikme ekliyor; `manipulation` bunu kapatıyor.
         Telefondan açıldığında arayüz gözle görülür şekilde daha çevik. */
      button, [role="button"], a, summary { touch-action: manipulation; }

      /* 3) AŞIRI KAYDIRMA ZIPLAMASI (rubber-band). Sayfanın sonuna gelince
         macOS boşluğa doğru kaydırmaya izin verip geri yaylanıyor; sohbet
         içeriği akarken bu yaylanma titreme olarak görünüyor. `contain`,
         kaydırmayı içeriğin gerçek sınırında durduruyor — zincirleme
         kaydırmayı da engelliyor, yani iç bir kutu bittiğinde sayfa
         kendiliğinden kaymıyor. */
      html, body,
      [data-testid="stAppViewContainer"],
      [data-testid="stMain"] {
        /* `contain` yaylanmayı azalttı ama tamamen bitirmedi; `none` sınırda
           hiç hareket bırakmıyor. */
        overscroll-behavior-y: none;
        /* Yumuşak kaydırma, sınıra çarpınca "yükselip geri gelme" olarak
           görünen animasyonu üretiyor. Sohbette anlık konumlanma isteniyor. */
        scroll-behavior: auto;
      }

      /* Soru kutusu her zaman altta sabit kalsın ve arkası saydam olmasın —
         metin altından geçerken kutu üzerine binmiş gibi görünüyordu. */
      /* KONUMLANDIRMAYA DOKUNULMUYOR. Kutuyu `position: fixed; left:0; right:0`
         ile sabitlemiştim; bu onu tüm ekran genişliğine yayıp kenar çubuğunu
         yok sayıyordu ve çubuk açılınca soru kutusu sohbetle hizasını
         kaybediyordu. Streamlit kutuyu zaten kenar çubuğuna göre
         konumlandırıyor — yalnızca görünümünü değiştiriyoruz.

         Çizgi yerine yumuşak geçiş: metin kutunun altına doğru silinerek
         giriyor. Sert bir kenarlık sayfayı ikiye bölüp sohbetin
         sürekliliğini kesiyordu. */
      [data-testid="stBottom"] {
        background: linear-gradient(
          180deg, transparent 0%, var(--ink) 18%, var(--ink) 100%
        );
      }
      [data-testid="stBottom"] > div { max-width: 46rem; margin: 0 auto; }

      /* ALT BOŞLUĞA DOKUNULMUYOR. `padding-bottom: 7rem` yazmıştım; bu
         soru kutusunun gerçek yüksekliğinden fazlaydı ve sohbetin sonuyla
         kutu arasında boşluk bırakıyordu. Streamlit bu boşluğu kutunun
         ölçülen yüksekliğine göre kendisi ayarlıyor — sabit bir sayı, kutu
         çok satırlı bir soruyla büyüdüğünde de yanlış olurdu. */
      [data-testid="stAppViewContainer"],
      [data-testid="stVerticalBlock"] { overflow-anchor: none; }

      /* Akış sırasında imleç karakteri satır yüksekliğini değiştirip
         zıplamaya yol açıyordu; sabit genişlikte bir kutuya alındı. */
      .stream-caret { display:inline-block; width:.5rem; opacity:.55; }

      /* --- başlık --------------------------------------------------- */
      .masthead { margin: 0 0 1.6rem; padding-bottom: 1.1rem;
                  border-bottom: 1px solid var(--line); position: relative; }
      .masthead::after { content:''; position:absolute; left:0; bottom:-1px;
                         width:4.5rem; height:1px; background:var(--signal); }
      .masthead h1 { font-family: var(--display); font-weight: 600;
                     font-size: 2.05rem; letter-spacing: -.022em; line-height: 1.1;
                     margin: 0 0 .3rem; color: var(--paper); }
      .masthead h1 .mark { color: var(--signal); font-weight: 400; }
      /* Logo başlık metniyle aynı optik ağırlıkta: yüksekliği harf
         yüksekliğine bağlı, böylece başlık boyutu değişirse birlikte
         ölçekleniyor. */
      .masthead h1 .mark-logo {
        height: .92em; width: auto; vertical-align: -.08em;
        margin-right: .12em;
      }
      .masthead p { font-family: var(--mono); font-size: .70rem; letter-spacing: .08em;
                    text-transform: uppercase; color: var(--muted); margin: 0; }

      /* --- cevap gövdesi: okumak için ------------------------------- */
      [data-testid="stChatMessage"] p,
      [data-testid="stChatMessage"] li {
        font-family: var(--serif); font-size: 1.035rem; line-height: 1.72;
        color: var(--paper); letter-spacing: .002em;
      }
      [data-testid="stChatMessage"] strong { color: #FFF8EC; font-weight: 500; }
      [data-testid="stChatMessage"] h1,
      [data-testid="stChatMessage"] h2,
      [data-testid="stChatMessage"] h3 {
        font-family: var(--display); font-weight: 600; letter-spacing: -.012em;
        font-size: 1.12rem; margin: 1.3rem 0 .5rem; color: var(--paper);
      }
      /* atıf numaraları metnin içinde göze çarpsın */
      [data-testid="stChatMessage"] p { text-wrap: pretty; }

      /* AVATARLAR: kutu değil, işaret.
         Streamlit özel avatarı yuvarlak köşeli renkli bir kutuya oturtuyor;
         o kutu koyu, sakin bir sayfada gereksiz ağırlık yapıyor. Zemin
         kaldırılıp yalnızca işaret bırakılıyor ve boyut metin satırıyla
         hizalanıyor. */
      /* SEÇİCİ ÖLÇÜLEREK YAZILDI. Streamlit özel avatarı `stChatMessageAvatar*`
         testid'iyle DEĞİL, mesaj kabının doğrudan çocuğu olan düz bir <img>
         ile veriyor (alt="user avatar" / "assistant avatar"). Testid'e yazılan
         ilk kural hiçbir şeyle eşleşmiyordu ve avatarlar Streamlit'in
         varsayılanıyla çiziliyordu. */
      [data-testid="stChatMessage"] > img[alt$="avatar"] {
        width: 1.4rem !important; height: 1.4rem !important;
        border-radius: 0 !important; background: transparent !important;
        object-fit: contain; opacity: .95;
        align-self: flex-start;
      }

      /* SORU KABI VE İŞARET AYNI SATIRDA.
         Asistan tarafında işaret rozet sırasının hizasında duruyor ve doğru;
         kullanıcı tarafında ise kabın içinde tek satır metin var, işaret onun
         ORTASINA gelmeli. Streamlit ikisini aynı kuralla çizdiği için hizayı
         mesaj türüne göre ayırmak gerekiyor. */
      [data-testid="stChatMessage"]:has(img[alt="user avatar"]) {
        align-items: center;
        padding: .7rem 1.05rem !important;
      }
      [data-testid="stChatMessage"]:has(img[alt="user avatar"]) > img {
        align-self: center;
      }
      /* Streamlit markdown kabına -16px alt kenar boşluğu veriyor (blokları
         sıkıştırmak için). Flex ortalaması gerçek yüksekliği değil o kısalmış
         kutuyu merkezliyordu ve işaret tam yarısı kadar, 8px yukarı kayıyordu.
         Ölçüldü: img merkezi -6985, metin merkezi -6976.5. */
      [data-testid="stChatMessage"]:has(img[alt="user avatar"])
      [data-testid="stMarkdownContainer"] {
        margin-bottom: 0 !important;
      }

      /* KULLANICININ SORUSU.
         Monospace bırakıldı: soru "kod gibi" değil, sistemin sesinden AYRI
         bir ses olarak okunmalı ve sabit genişlik bunu en net yapan araç.
         Ama boyut ve renk yeniden ayarlandı — eskiden gövde metninden küçüktü
         ve sayfadaki en doygun rengi taşıyordu; ikisi birden onu hem zayıf
         hem gürültülü yapıyordu. */
      [data-testid="stChatMessage"]:has(img[alt="user avatar"]) p {
        font-family: var(--mono); font-size: .88rem; line-height: 1.6;
        color: var(--signal); letter-spacing: -.01em;
        margin: 0;
      }

      /* --- model seçici hapı ----------------------------------------- */
      /*
         Giriş kutusunun SAĞ üstüne yapışık, küçük ve sessiz. Her soruda
         değiştirilecek bir ayar değil — gerektiğinde hatırlanacak bir seçenek;
         boyutu bunu söylemeli.

         `justify-content: flex-end` tek başına yetmiyordu: Streamlit'in iç
         sarmalayıcısı tam genişlikte, dolayısıyla hizalanacak boşluk kalmıyor.
         `margin-left: auto` sarmalayıcının kendisini sağa itiyor.

         `position: sticky` kullanılıyor, `fixed` DEĞİL: sabit konumlandırma
         öğeyi görüntü alanına bağlıyor ve kenar çubuğu açıldığında hizası
         kayıyor (bugün soru kutusunda yaşandı).
      */
      /* Sabit çubuğun içinde: `sticky` gerekmiyor, kap zaten ekrana bağlı. */
      [class*="st-key-modepill"] {
        margin: 0 0 .25rem; pointer-events: none;
      }

      [class*="st-key-modepill"] > div,
      [class*="st-key-modepill"] [data-testid="stPopover"] {
        width: auto !important; margin-left: auto; pointer-events: auto;
      }
      [class*="st-key-modepill"] [data-testid="stPopoverButton"] {
        background: var(--panel); border: 1px solid var(--line);
        color: var(--muted); border-radius: 999px;
        padding: .1rem .7rem; min-height: 0; height: 32px; width: auto;
        font-family: var(--mono); font-size: .64rem; letter-spacing: .02em;
        cursor: pointer; white-space: nowrap; box-shadow: none;
      }
      [class*="st-key-modepill"] [data-testid="stPopoverButton"]:hover {
        color: var(--paper); border-color: #39414D;
      }
      [class*="st-key-modepill"] [data-testid="stPopoverButton"]:focus-visible {
        outline: 2px solid var(--signal); outline-offset: 2px;
      }
      [class*="st-key-modepill"] [data-testid="stPopoverButton"] svg {
        width: .72rem; height: .72rem;
      }

      /* Açılır liste --------------------------------------------------
         İki tuzak var ve ikisi de bugün stil kaybına yol açtı:

         1) Streamlit'in butonu `stButton` sarmalayıcısının DOĞRUDAN çocuğu
            değil — arada bir ipucu sarmalayıcısı var. `>` birleştiricisi
            bu yüzden hiçbir zaman eşleşmiyor; alt öğe seçici gerekiyor.
         2) Panel `#stFloatingOverlayPortal` içinde, uygulama ağacının
            dışında render ediliyor. Seçiciler global tutuluyor, kapsayıcıya
            bağlanmıyor.

         `!important`, emotion'ın aynı ağırlıktaki sınıf kurallarıyla
         berabere kalıp sıraya bağlı kazanmasını engelliyor. */
      /* `[data-st-overlay-root]` bir yedek olarak eklenmişti; Streamlit'in
         BOŞ `#portal` kabı da o niteliği taşıyor ve kural ona panel zemini
         veriyordu — sayfanın sol üstünde 240px'lik (15rem) koyu bir kutu
         belirmesinin sebebi buydu. `stPopoverBody` tek başına yetiyor. */
      [data-testid="stPopoverBody"] {
        min-width: 15rem !important; max-width: 21rem !important;
        max-height: none !important; height: auto !important;
        overflow: visible !important;
        /* PANEL NEDEN BU KADAR YÜKSEK.
           Hapın altında — sabit çubuğun geometrisi gereği, pencere boyundan
           bağımsız olarak — 134px boşluk var. Panel oraya sığdığı sürece
           floating-ui onu AŞAĞI açıyor ve giriş kutusunu örtüyor. Sığmayınca
           kendi kararıyla yukarı çeviriyor. Yönü taklit etmek yerine (önceki
           denemede elle kaydırma, tarayıcının kendi çevirmesiyle üst üste
           binip paneli sayfanın ortasına atmıştı) girdiyi değiştiriyoruz. */
        min-height: 170px !important;
        padding: .3rem !important;
        background: var(--panel) !important;
        border: 1px solid var(--line) !important;
        border-radius: 10px !important;
        box-shadow: 0 10px 30px rgba(0,0,0,.45) !important;
      }
      [data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] {
        gap: 0 !important;
      }
      [data-testid="stPopoverBody"] button,
      [data-testid="stPopoverBody"] [data-testid^="stBaseButton"] {
        width: 100% !important;
        display: flex !important; justify-content: flex-start !important;
        text-align: left !important;
        background: transparent !important; border: none !important;
        box-shadow: none !important;
        font-family: var(--serif) !important; font-size: .9rem !important;
        color: var(--paper) !important;
        padding: .95rem .6rem 1rem !important;
        min-height: 0 !important; height: auto !important;
        border-radius: 6px !important; cursor: pointer !important;
      }
      /* Etiket, butonun İÇİNDEKİ div'de yaşıyor ve o div de flex —
         `text-align` oraya işlemiyor, hizayı `justify-content` veriyor. */
      [data-testid="stPopoverBody"] button div,
      [data-testid="stPopoverBody"] button p {
        justify-content: flex-start !important; text-align: left !important;
        width: 100% !important;
      }
      /* İki satırın ayrımı yazı tipiyle: başlık okunur, açıklama sönük. */
      [data-testid="stPopoverBody"] button p {
        font-family: var(--serif) !important; font-size: .74rem !important;
        color: var(--muted) !important; line-height: 1.5 !important;
        margin: 0 !important;
      }
      [data-testid="stPopoverBody"] button p strong {
        font-size: .92rem !important; font-weight: 500 !important;
        color: var(--paper) !important;
      }
      [data-testid="stPopoverBody"] button:hover,
      [data-testid="stPopoverBody"] [data-testid^="stBaseButton"]:hover {
        background: var(--panel-2) !important;
      }

      /* Eylem sırası KENDİ KABINA kapsanıyor. Önce sohbet mesajı içindeki
         her sütuna yazılıydı; quiz alt çubuğu da sütun kullandığı için
         "Yeniden çöz" 40px'lik ikon kutusuna dönüştü ve etiketi taştı.
         Aynı hata daha önce sohbet listesinde "Vazgeç" ile yaşanmıştı —
         `st.container(key=...)` ile kapsamak tek güvenilir yol. */

      /* --- tablolar ----------------------------------------------------
         Çapraz sentez karşılaştırma tablosu üretiyor ve üç belgeli bir tablo
         sohbet sütunundan geniş olabiliyor. Sayfa yatay kaymamalı; tablo kendi
         kabı içinde kayıyor. */
      [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] table {
        display: block; overflow-x: auto; max-width: 100%;
      }

      /* --- ilerleme göstergesi --------------------------------------- */
      /* Tamamlanan adımlar sönük ve sabit; çalışan adım vurgulu ve nabız
         atıyor. Nabız YALNIZCA aktif satırda — sürekli hareket eden bir
         arayüz dikkat dağıtıyor, tek bir canlı nokta ise "çalışıyor"u
         söylüyor. */
      .pstep { font-family: var(--mono); font-size: .74rem; line-height: 1.9;
               color: var(--muted); display: flex; align-items: baseline; gap: .4rem; }
      .pstep.done { opacity: .45; }
      .pstep.live { color: var(--paper); }
      .pwhy { font-family: var(--serif); font-size: .78rem; color: var(--muted);
              margin-left: .3rem; }
      .pdot { width: 6px; height: 6px; border-radius: 50%;
              background: var(--signal); display: inline-block; flex: none;
              animation: pulse 1.1s ease-in-out infinite; }
      @keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: .25 } }
      .pelapsed { font-family: var(--mono); font-size: .66rem; color: #5F646D;
                  margin-top: .35rem; }
      @media (prefers-reduced-motion: reduce) {
        .pdot { animation: none; }
      }

      /* --- cevap içindeki tablo ve listeler -------------------------- */
      /* Tablo, karşılaştırmayı taramayı okumaktan hızlı kılıyor; ama cevabın
         içinde bir "veri ızgarası" gibi durmamalı. Bu yüzden çerçeve yok,
         yalnızca başlık altı çizgisi ve satır ayraçları — editoryal bir
         tablo, arayüz bileşeni değil. */
      [data-testid="stChatMessage"] table {
        width: 100%; border-collapse: collapse; margin: .9rem 0;
        font-family: var(--serif); font-size: .88rem; line-height: 1.5;
        display: block; overflow-x: auto;      /* dar ekranda taşmasın */
        /* KAYMA İPUCU. Tablo kabından genişse sağ kenarda bir gölge beliriyor,
           sona gelince kayboluyor (`local` zemin içerikle kayıyor ve gölgeyi
           örtüyor). Önceki hâlinde üçüncü sütun kesik duruyordu ve yana
           kaydırılabildiğini söyleyen hiçbir şey yoktu. */
        background:
          linear-gradient(90deg, rgba(12,14,17,0), var(--ink) 70%) 100% 0 / 2.5rem 100% no-repeat local,
          radial-gradient(farthest-side at 100% 50%, rgba(233,168,92,.22), transparent) 100% 0 / .6rem 100% no-repeat scroll;
      }
      [data-testid="stChatMessage"] thead th {
        text-align: left; font-family: var(--mono); font-weight: 500;
        font-size: .66rem; letter-spacing: .05em; text-transform: uppercase;
        color: var(--signal); padding: .35rem .8rem .35rem 0;
        border-bottom: 1px solid var(--signal-d);
        /* `nowrap` KALDIRILDI: model "Kuzey Lojistik A.Ş. Tedarik Sözleşmesi"
           gibi uzun başlıklar yazıyor ve tek satırda tutulunca tabloyu sohbet
           sütununun iki katına çıkarıyordu. Sütunun alt sınırı ise
           `min-width` ile korunuyor, yoksa hücreler tek kelimelik şeritlere
           dönüşüyor. */
        white-space: normal; vertical-align: bottom; min-width: 7.5rem;
      }
      [data-testid="stChatMessage"] tbody td {
        padding: .5rem .8rem .5rem 0; vertical-align: top;
        border-bottom: 1px solid var(--line); color: var(--paper);
        min-width: 7.5rem; overflow-wrap: anywhere;
      }
      [data-testid="stChatMessage"] tbody tr:last-child td { border-bottom: none; }
      [data-testid="stChatMessage"] tbody td:first-child { color: #FFF8EC; }

      /* Etiketli liste: kalın etiket tarama çengeli, gerisi açıklama */
      [data-testid="stChatMessage"] ul { margin: .7rem 0; padding-left: 1.1rem; }
      [data-testid="stChatMessage"] li { margin: .35rem 0; }
      [data-testid="stChatMessage"] li::marker { color: var(--signal-d); }

      /* --- telemetri rozetleri -------------------------------------- */
      .badge-row { display:flex; flex-wrap:wrap; gap:.32rem; margin:.15rem 0 .95rem; }
      .badge { font-family: var(--mono); font-size:.665rem; letter-spacing:.03em;
               padding:.2rem .5rem; border-radius:2px; white-space:nowrap;
               color: var(--muted); background: var(--panel);
               border:1px solid var(--line); }
      .badge-primary { color: var(--paper); border-color:#39414D; }
      .badge-cache   { color:#0C0E11; background:var(--ok); border-color:var(--ok);
                       font-weight:500; }
      .badge-strong  { color: var(--signal); border-color: var(--signal-d);
                       background: rgba(233,168,92,.09); }
      .badge-warn    { color: var(--warn); border-color:#5B322C;
                       background: rgba(217,122,108,.09); }

      /* --- cevap altındaki eylem sırası ------------------------------ */
      /*
         Streamlit butonları varsayılan olarak kutulu ve geniş; istenen ise
         metnin altında sessizce duran ikonlar. Ama "sessiz" ile "erişilemez"
         farklı şeyler — ilk denememde ikisi karışmıştı:

           · min-height: 0 ile buton ~20px oluyordu. Dokunmatik hedef en az
             36-44px olmalı (WCAG); parmakla ıskalanan bir buton görsel olarak
             ne kadar zarif olursa olsun bozuktur.
           · Görünür odak halkası yoktu. Klavyeyle gezen biri hangi butonda
             olduğunu göremiyordu.
           · opacity .45 metin rengiyle birleşince kontrast 4.5:1'in altına
             düşüyordu.

         Çözüm: buton KUTUSU 40px kalıyor (dokunma hedefi), ikon küçük ve
         sönük duruyor (görsel sessizlik). İkisi aynı şey değil.
      */
      [class*="st-key-actionrow"] [data-testid="stButton"] button {
        background: transparent; border: none; box-shadow: none;
        width: 40px; height: 40px; min-height: 40px; padding: 0;
        display: inline-flex; align-items: center; justify-content: center;
        border-radius: 6px;
        color: var(--muted);
        transition: color .12s ease, background .12s ease;
      }
      [class*="st-key-actionrow"] [data-testid="stButton"] button:hover:not(:disabled) {
        color: var(--paper); background: var(--panel);
      }
      /* Basılı hâli: tıklamanın kaydedildiğini ANINDA söylüyor. Ağ gecikmesi
         100 ms'yi geçtiğinde bu geri bildirim olmadan kullanıcı ikinci kez
         basıyor. */
      [class*="st-key-actionrow"] [data-testid="stButton"] button:active:not(:disabled) {
        transform: scale(.92); background: var(--panel-2);
      }
      /* Tıklanabilirlik imleçten okunur; kenarlığı ve zemini kaldırdığımız
         için başka görsel ipucu kalmadı. Devre dışı olan da farklı imleç
         göstermeli, yoksa "tıkladım ama olmadı" hissi doğuyor. */
      [class*="st-key-actionrow"] [data-testid="stButton"] button { cursor: pointer; }
      [class*="st-key-actionrow"] [data-testid="stButton"] button:disabled { cursor: not-allowed; }
      [class*="st-key-convrow"] button { cursor: pointer; }
      [class*="st-key-actionrow"] [data-testid="stButton"] button:disabled {
        /* ÖLÇÜLDÜ: #4A4F58 zeminle 2.35:1 veriyordu — devre dışı öğeler için
           gereken 3:1'in altında, yani ikon görünmüyor sayılırdı. #5F646D
           3.25:1 sağlıyor: hâlâ edilgen duruyor ama seçilebiliyor. */
        color: #5F646D;
      }
      /* Klavye odağı görünür olmalı — fare kullanıcısını rahatsız etmeden:
         `:focus-visible` yalnızca klavyeyle gezildiğinde tetikleniyor. */
      [class*="st-key-actionrow"] [data-testid="stButton"] button:focus-visible {
        outline: 2px solid var(--signal); outline-offset: 2px; color: var(--paper);
      }
      [class*="st-key-actionrow"] [data-testid="stButton"] button span {
        font-size: 1.05rem; line-height: 1;
      }
      /* Sıra, cevaba yapışık dursun — araya Streamlit'in boşluğu girmesin */
      [data-testid="stChatMessage"] [data-testid="stHorizontalBlock"] {
        /* Bitişik dokunma hedefleri arasında en az 8px boşluk olmalı; .1rem
           (1.6px) parmakla yanlış düğmeye basmayı davet ediyordu. 40px'lik
           kutular zaten kendi içlerinde boşluk taşıyor, aradaki 8px onları
           ayırıyor. */
        gap: .5rem !important; margin-top: -.2rem;
      }
      /* ARAÇ İPUÇLARI GİZLİ, ERİŞİLEBİLİR AD DURUYOR.
         `help` metni kaldırılsaydı yalnızca ikon taşıyan bu butonların ekran
         okuyucuya söyleyeceği hiçbir şey kalmazdı. Bu yüzden metin yerinde
         bırakılıp yalnızca beliren balon gizleniyor: gören kullanıcı temiz
         bir sıra görüyor, görmeyen kullanıcı butonun ne yaptığını duyuyor. */
      [data-testid="stTooltipContent"] { display: none !important; }

      /* Streamlit, bazı öğelerin üzerine gelince kendi araç çubuğunu (kopyala,
         tam ekran) gösteriyor. Cevabın altında zaten kendi kopyala düğmemiz
         var; ikinci bir kopyala, hangisinin ne kopyaladığını belirsizleştirip
         eylem sırasının üstünde asılı duruyordu. */
      [data-testid="stElementToolbar"] { display: none !important; }

      /* Hareketi azaltma tercihi olan kullanıcıda geçişler kapansın. */
      @media (prefers-reduced-motion: reduce) {
        [class*="st-key-actionrow"] [data-testid="stButton"] button { transition: none; }
      }

      /* --- kenar çubuğu: sohbet listesi ------------------------------ */
      /*
         Liste tarama içindir: göz aşağı inerken başlıkları okur, eyleme
         yalnızca hedefini bulunca geçer. Bu yüzden silme ikonu her satırda
         DURUYOR ama GÖRÜNMÜYOR — satırın üzerine gelince beliriyor. Hepsini
         sürekli göstermek listeyi çöp kutusu sırasına çevirip başlıkların
         okunmasını zorlaştırıyordu.

         İkon DOM'dan kaldırılmıyor, yalnızca saydamlığı sıfır: klavyeyle
         gezen kullanıcı ona sekme ile ulaşabilmeli ve odaklandığında
         görünmeli (aşağıdaki :focus-within kuralı).
      */
      /*
         SEÇİCİLER `st-key-convrow…` İLE KAPSANIYOR. Önce `stSidebar` altındaki
         tüm sütunlara yazmıştım; onay satırı da sütun kullandığı için "Vazgeç"
         butonu 32px görünmez bir ikon kutusuna dönüşüp kayboldu. Streamlit,
         `st.container(key=...)` için DOM'a `st-key-<ad>` sınıfı koyuyor —
         iki satır türünü kesin ayırmanın doğru yolu bu.
      */
      /* Streamlit'in varsayılan ikincil butonu 1px kenarlıklı; kenar
         çubuğunun üstünde bu kutu, altındaki sessiz sohbet listesiyle
         çelişen bir çerçeve çiziyordu. */
      [class*="st-key-newchat"] [data-testid="stButton"] button {
        justify-content: flex-start; text-align: left;
        background: transparent; border: none; box-shadow: none;
        padding: .4rem .5rem; min-height: 36px;
        font-family: var(--mono); font-size: .72rem; letter-spacing: .04em;
        color: var(--signal);
      }
      [class*="st-key-newchat"] [data-testid="stButton"] button:hover {
        background: var(--panel); color: var(--signal);
      }
      [class*="st-key-newchat"] [data-testid="stButton"] button:focus-visible {
        outline: 2px solid var(--signal); outline-offset: 2px;
      }

      /* Belge satırı: sohbet satırıyla aynı görünüm. Ad tıklanınca bilgi
         kartı açılıyor. */
      [class*="st-key-docrow"] [data-testid="stHorizontalBlock"] {
        gap: .15rem !important; align-items: center;
      }
      [class*="st-key-docrow"] [data-testid="stColumn"]:first-child [data-testid="stButton"] button {
        justify-content: flex-start; text-align: left;
        background: transparent; border: none; box-shadow: none;
        padding: .4rem .5rem; min-height: 40px;
        font-size: .78rem; line-height: 1.3; color: var(--paper);
      }
      [class*="st-key-docrow"] [data-testid="stColumn"]:first-child [data-testid="stButton"] button:hover {
        background: var(--panel);
      }
      /* Ad İKİ SATIRA kadar sarılıyor. Tek satırda "Örnek Teknoloji Hizmetl…"
         gibi kesiliyordu ve beş belgenin üçü aynı önekle başladığında satırlar
         birbirinden ayırt edilemiyordu. */
      [class*="st-key-docrow"] [data-testid="stColumn"]:first-child [data-testid="stButton"] button p {
        white-space: normal; text-align: left; line-height: 1.35;
        display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
        overflow: hidden; overflow-wrap: anywhere;
      }
      /* Bilgi kartı: satırın hemen altında, sol çizgiyle ona bağlı. */
      [class*="st-key-docinfo"] {
        margin: -.2rem 0 .5rem .5rem; padding: .15rem 0 .1rem .7rem;
        border-left: 2px solid var(--line);
      }
      .doc-info { font-family: var(--mono); font-size: .64rem; line-height: 1.75;
                  color: var(--paper); margin-bottom: .35rem; }
      .doc-info div { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .doc-info span { display: inline-block; width: 4.6rem; color: var(--muted);
                       letter-spacing: .03em; }
      [class*="st-key-docinfo"] [data-testid="stButton"] button {
        background: transparent; border: 1px solid var(--line); box-shadow: none;
        min-height: 36px; padding: .2rem .6rem; color: var(--signal);
        font-family: var(--mono); font-size: .66rem; letter-spacing: .03em;
      }
      [class*="st-key-docinfo"] [data-testid="stButton"] button:hover {
        border-color: var(--signal); background: rgba(233,168,92,.08); color: var(--signal);
      }
      /* Odak işareti: sohbet kutusunun üstünde, model seçicinin solunda.
         Tek bir düğme — tıklanınca odak kalkıyor; ✕ bunu söylüyor. */
      [class*="st-key-focuschip"] [data-testid="stButton"] button {
        border-radius: 999px; height: 32px; min-height: 0; padding: .1rem .75rem;
        background: rgba(233,168,92,.10); border: 1px solid rgba(233,168,92,.45);
        color: var(--signal); box-shadow: none; max-width: 100%;
        font-family: var(--mono); font-size: .64rem; letter-spacing: .02em;
      }
      [class*="st-key-focuschip"] [data-testid="stButton"] button p {
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      [class*="st-key-focuschip"] [data-testid="stButton"] button:hover {
        background: rgba(233,168,92,.18); border-color: var(--signal);
      }
      [class*="st-key-focuschip"] [data-testid="stButton"] button:focus-visible,
      [class*="st-key-docinfo"] [data-testid="stButton"] button:focus-visible,
      [class*="st-key-docrow"] [data-testid="stButton"] button:focus-visible {
        outline: 2px solid var(--signal); outline-offset: 2px;
      }
      [class*="st-key-focuschip"] [data-testid="stButton"] button [data-testid="stIconMaterial"] {
        font-size: .9rem;
      }
      [class*="st-key-docrow"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button {
        background: transparent; border: none; box-shadow: none;
        width: 32px; height: 32px; min-height: 32px; padding: 0;
        color: var(--muted); opacity: 0; transition: opacity .12s ease, color .12s ease;
      }
      [class*="st-key-docrow"]:hover [data-testid="stColumn"]:last-child [data-testid="stButton"] button,
      [class*="st-key-docrow"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button:focus-visible {
        opacity: 1;
      }
      [class*="st-key-docrow"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button:hover {
        color: var(--warn);
      }

      [class*="st-key-convrow"] [data-testid="stHorizontalBlock"] {
        gap: .15rem !important; align-items: center;
      }
      /* Başlık butonu: sola yaslı, tek satır, taşarsa "…" */
      [class*="st-key-convrow"] [data-testid="stColumn"]:first-child [data-testid="stButton"] button {
        justify-content: flex-start; text-align: left;
        background: transparent; border: none; box-shadow: none;
        padding: .4rem .5rem; min-height: 36px;
        font-size: .78rem; line-height: 1.3; color: var(--paper);
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      [class*="st-key-convrow"] [data-testid="stColumn"]:first-child [data-testid="stButton"] button:hover {
        background: var(--panel);
      }
      /* Silme ikonu: normalde görünmez, satır hedeflendiğinde beliriyor.
         DOM'dan kaldırılmıyor — klavyeyle ulaşılabilmeli. */
      [class*="st-key-convrow"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button {
        background: transparent; border: none; box-shadow: none;
        width: 32px; height: 32px; min-height: 32px; padding: 0;
        color: var(--muted); opacity: 0; transition: opacity .12s ease, color .12s ease;
      }
      [class*="st-key-convrow"]:hover [data-testid="stColumn"]:last-child [data-testid="stButton"] button,
      [class*="st-key-convrow"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button:focus-visible {
        opacity: 1;
      }
      [class*="st-key-convrow"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button:hover {
        color: var(--warn);      /* yıkıcı eylem: rengiyle uyarıyor */
      }
      [class*="st-key-convrow"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button:focus-visible {
        outline: 2px solid var(--signal); outline-offset: 2px;
      }

      /* Onay satırı: iki buton YAN YANA ve ikisi de tam görünür. */
      [class*="st-key-confirm"] [data-testid="stHorizontalBlock"] {
        gap: .35rem !important; flex-wrap: nowrap;
      }
      [class*="st-key-confirm"] [data-testid="stColumn"] { min-width: 0; }
      [class*="st-key-confirm"] button {
        width: 100%; min-height: 32px; padding: .3rem .4rem;
        font-family: var(--mono); font-size: .7rem; opacity: 1;
      }
      .conv-confirm {
        font-family: var(--mono); font-size: .7rem; line-height: 1.45;
        color: var(--warn); padding: .5rem .5rem .35rem;
      }
      @media (prefers-reduced-motion: reduce) {
        [class*="st-key-convrow"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button {
          transition: none;
        }
      }

      /* Sayfa düğmeleri: kaynak bloğunun altında, sessiz ve küçük. */
      [class*="st-key-sayfabtn"] [data-testid="stHorizontalBlock"] {
        gap: .35rem !important; margin: .1rem 0 .3rem 1.85rem;
      }
      [class*="st-key-sayfabtn"] [data-testid="stButton"] button {
        background: transparent; border: 1px solid var(--line); box-shadow: none;
        min-height: 32px; padding: .1rem .5rem; color: var(--muted);
        font-family: var(--mono); font-size: .64rem; letter-spacing: .02em;
      }
      [class*="st-key-sayfabtn"] [data-testid="stButton"] button:hover {
        border-color: var(--signal-d); color: var(--signal);
      }
      [class*="st-key-sayfabtn"] [data-testid="stButton"] button:focus-visible {
        outline: 2px solid var(--signal); outline-offset: 2px;
      }
      [class*="st-key-sayfabtn"] [data-testid="stButton"] button [data-testid="stIconMaterial"] {
        font-size: .8rem;
      }
      /* Sayfa görüntüsü: belgenin aslı, bir kâğıt gibi çerçeveli. */
      [data-testid="stChatMessage"] [data-testid="stImage"] img {
        border: 1px solid var(--line); border-radius: 2px; background: #fff;
      }

      /* --- kaynak pasajları ----------------------------------------- */
      .passage { font-family: var(--serif); font-size:.90rem; line-height:1.62;
                 color:#CFC9BE; background:var(--panel-2);
                 border:1px solid var(--line); border-left:2px solid var(--signal-d);
                 padding:.6rem .8rem; margin:.5rem 0; border-radius:0 2px 2px 0; }
      .passage-head { display:block; font-family: var(--mono); font-size:.63rem;
                      letter-spacing:.07em; text-transform:uppercase;
                      color: var(--signal); margin-bottom:.4rem; }
      .src-title { font-family: var(--serif); font-size:.97rem; line-height:1.45; }
      .src-meta  { font-family: var(--mono); font-size:.65rem; letter-spacing:.04em;
                   color: var(--muted); }
      .src-n { font-family: var(--mono); color: var(--signal); font-size:.8rem;
               margin-right:.35rem; }

      /* --- "bu cevap nasıl üretildi" -------------------------------- */
      /* Adım adı önde ve okunur; sayılar sağda, sönük ve monospace. Amaç
         hesap vermek, hata ayıklamak değil — bu yüzden hiyerarşi cümleden
         yana. Çubuk, hangi adımın zamanı yediğini okumadan gösteriyor. */
      .tstep { padding:.5rem 0; border-bottom:1px solid var(--line); }
      .tstep:last-child { border-bottom:none; }
      .tstep-head { display:flex; justify-content:space-between;
                    align-items:baseline; gap:.75rem; }
      .tstep-name { font-family: var(--serif); font-size:.9rem; color: var(--paper); }
      .tstep-num  { font-family: var(--mono); font-size:.66rem; color: var(--muted);
                    white-space:nowrap; }
      .tbar { height:2px; background: var(--panel); border-radius:1px; margin:.35rem 0; }
      .tbar > i { display:block; height:100%; background: var(--signal-d);
                  border-radius:1px; }
      .tstep-why { font-family: var(--serif); font-size:.8rem; line-height:1.5;
                   color:#9A948A; }
      .tstep-detail { font-family: var(--mono); font-size:.65rem; color: var(--muted); }
      .tnotes { margin-top:.8rem; padding-top:.7rem; border-top:1px solid var(--line);
                font-family: var(--mono); font-size:.67rem; line-height:1.7;
                color: var(--muted); }

      /* --- expander & kenar çubuğu ---------------------------------- */
      [data-testid="stExpander"] summary p { font-family: var(--mono);
        font-size:.72rem; letter-spacing:.05em; color: var(--muted); }
      /* Kenar çubuğunda monospace İSTİYORUZ ama `*` ile vermek OLMAZ:
         Streamlit'in ikonları ligatürlü bir simge fontu (Material Symbols)
         kullanıyor ve font ailesi ezilince ligatür çözülmüyor — ikonun ADI
         düz metin olarak basılıyor ("keyboard_double_arrow_left", "arrow_right")
         ve başlıkların üstüne biniyor. Bu yüzden yalnızca metin taşıyan
         öğeler hedefleniyor, ikonlara dokunulmuyor. */
      [data-testid="stSidebar"] p,
      [data-testid="stSidebar"] label,
      [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
      [data-testid="stSidebar"] h3, [data-testid="stSidebar"] button div,
      [data-testid="stSidebar"] [data-testid="stMetricValue"],
      [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
        font-family: var(--mono);
      }
      /* Güvenlik ağı: ikon fontu her koşulda geri alınır. */
      [data-testid="stIconMaterial"], span[class*="material-symbols"] {
        font-family: 'Material Symbols Rounded', 'Material Symbols Outlined' !important;
        font-feature-settings: 'liga' !important;
      }
      [data-testid="stSidebar"] [data-testid="stMetricValue"] {
        font-size:1.15rem; color: var(--paper); }
      [data-testid="stSidebar"] [data-testid="stMetricLabel"] p {
        font-size:.62rem; letter-spacing:.07em; text-transform:uppercase; }

      /* --- soru kutusu ---------------------------------------------- */
      [data-testid="stChatInput"] textarea {
        font-family: var(--serif) !important; font-size:1rem !important; }

      /* --- boş durum ------------------------------------------------ */
      .empty { border:1px solid var(--line); border-radius:3px; padding:1.4rem 1.5rem;
               background: linear-gradient(180deg, var(--panel) 0%, var(--ink) 100%); }
      .empty h4 { font-family: var(--display); font-weight:600; font-size:1rem;
                  color: var(--paper); margin:0 0 .55rem; }
      .empty p  { font-family: var(--serif); font-size:.93rem; line-height:1.65;
                  color:#B8B2A7; margin:0 0 1rem; }
      .chip { display:inline-block; font-family: var(--mono); font-size:.70rem;
              color: var(--paper); background: var(--panel-2);
              border:1px solid var(--line); border-radius:2px;
              padding:.34rem .6rem; margin:.2rem .3rem .2rem 0; }
      .chip b { color: var(--signal); font-weight:400; }
      .empty-note { font-family: var(--mono); font-size:.66rem; letter-spacing:.05em;
                    color:#5F646D; margin-top:1rem; }
      footer, #MainMenu { visibility: hidden; }

      /* --- PDF yükleyici: Türkçe metin ----------------------------------
         Streamlit düğmeyi ("Upload") ve açıklamayı ("50MB per file • PDF")
         sabit İngilizce basıyor ve değiştirmek için bir parametre yok. Metin
         düğümleri görünmez yapılıp yerine CSS içeriği yazılıyor; DOM'daki
         öğeler ve tıklama davranışı olduğu gibi kalıyor. */
      [data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p {
        font-size: 0 !important; line-height: 0;
      }
      [data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p::after {
        content: "PDF seç"; font-size: .78rem; line-height: 1.4;
      }
      [data-testid="stFileUploaderDropzoneInstructions"] span {
        font-size: 0 !important;
      }
      /* SINIRI AŞAN DOSYA. Streamlit dosyayı tarayıcıda, sunucuya hiç
         göndermeden reddediyor (maxUploadSize). Ama sebebi yalnızca üzerine
         gelince çıkan İngilizce bir ipucunda yazıyor ve ipuçları bu sayfada
         gizli — kullanıcı açıklamasız kırmızı bir kutu görüyordu. CSS metnin
         içeriğine bakamadığı için tür ve boyut hatası tek cümlede. */
      [data-testid="stFileChips"]:has([aria-invalid="true"])::after {
        content: "Yüklenmedi — yalnızca PDF, en fazla 100 MB.";
        display: block; flex-basis: 100%; margin-top: .45rem;
        font-family: var(--mono); font-size: .68rem; line-height: 1.45;
        color: var(--warn);
      }
      [data-testid="stFileUploaderDropzoneInstructions"] span::after {
        content: "ya da sürükleyin";
        font-family: var(--mono); font-size: .66rem; letter-spacing: .02em;
        color: var(--muted);
      }

      /* --- dokunmatik ekran -------------------------------------------
         Silme ikonları fareyle üzerine gelinince beliriyor; dokunmatik
         ekranda "üzerine gelmek" yok ve ikonlara hiç ulaşılamıyordu (16
         görünmez düğme ölçüldü). Orada sürekli görünür ama sönük duruyorlar.
         Hedefler de parmak boyuna (44px) büyüyor. */
      @media (hover: none), (pointer: coarse) {
        [class*="st-key-docrow"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button,
        [class*="st-key-convrow"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button {
          opacity: .7; width: 44px; height: 44px; min-height: 44px;
        }
        [class*="st-key-docrow"] [data-testid="stColumn"]:first-child [data-testid="stButton"] button,
        [class*="st-key-convrow"] [data-testid="stColumn"]:first-child [data-testid="stButton"] button,
        [class*="st-key-newchat"] [data-testid="stButton"] button,
        [class*="st-key-docinfo"] [data-testid="stButton"] button,
        [class*="st-key-confirm"] button, [class*="st-key-docconfirm"] button {
          min-height: 44px;
        }
        [class*="st-key-focuschip"] [data-testid="stButton"] button,
        [class*="st-key-modepill"] [data-testid="stPopoverButton"] {
          height: 44px;
        }
      }

      /* --- dar ekran: satır sütunları yığılmasın -----------------------
         Streamlit 640px altında sütunları alt alta diziyor. Belge ve sohbet
         satırında bu, görünmez silme sütununu adın ALTINA atıp satırlar
         arasında 50px'lik boşluklar açıyordu; odak çipi ve model hapı da
         iki ayrı satıra düşüyordu. Bu kaplarda sütunlar yan yana kalıyor. */
      @media (max-width: 640px) {
        [class*="st-key-docrow"] [data-testid="stHorizontalBlock"],
        [class*="st-key-convrow"] [data-testid="stHorizontalBlock"],
        [data-testid="stBottom"] [data-testid="stHorizontalBlock"] {
          flex-wrap: nowrap !important;
        }
        [class*="st-key-docrow"] [data-testid="stColumn"],
        [class*="st-key-convrow"] [data-testid="stColumn"],
        [data-testid="stBottom"] [data-testid="stColumn"] {
          min-width: 0 !important;
        }
        [class*="st-key-docrow"] [data-testid="stColumn"]:last-child,
        [class*="st-key-convrow"] [data-testid="stColumn"]:last-child {
          flex: 0 0 44px !important; width: 44px !important;
        }
        [data-testid="stBottom"] [data-testid="stColumn"]:last-child {
          flex: 0 0 auto !important; width: auto !important;
        }
        [data-testid="stBottom"] [data-testid="stColumn"]:first-child {
          flex: 1 1 auto !important;
        }
      }
      .src-meta-line { display: inline-block; margin-left: 1.85rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- ısıtma ----------------------------------------------------------------


@st.cache_resource
def _warm_up() -> dict:
    """Ağır kurulumları arka planda, kullanıcı soru yazarken yapar.

    ÖLÇÜLDÜ 2026-09-01 — ısıtma yokken ilk sorgunun dökümü:
        ilk arama     41.6 sn   <- BGE-M3 yükleniyor (Network Asistanı'nda)
        generate       57.1 sn
        TOPLAM        102.4 sn   >  limits.max_wall_seconds (90)
    Duvar saati aşılınca devre kesici doğrulama düğümlerini atladı ve cevap
    "doğrulanamadı" etiketiyle döndü — yani soğuk başlangıç yalnızca yavaşlık
    değil, KALİTE kaybıydı.

    Bedel kaybolmuyor, yeri değişiyor: kullanıcının ilk sorusundan alınıp
    uygulamanın açılışına yazılıyor. `st.cache_resource` sayesinde süreç
    başına bir kez çalışıyor; iş parçacığı arka planda döndüğü için arayüz
    beklemiyor.
    """
    import threading

    state = {"started": time.time(), "done": False, "error": None}

    def run() -> None:
        try:
            from belge import embedder

            embedder.warm()          # BGE-M3'ü belleğe al
            get_store().load()       # indeksi ve BM25 sözlüğünü kur
        except Exception as exc:     # ısıtma başarısız olursa akış yine çalışır
            state["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            state["done"] = True

    threading.Thread(target=run, daemon=True).start()
    return state


_warm = _warm_up()


# --- durum -----------------------------------------------------------------

# --- oturum teşhisi --------------------------------------------------------
#
# Sohbet geçmişi `st.session_state` içinde duruyor ve o da tarayıcı bağlantısına
# bağlı: bağlantı düşerse Streamlit YENİ bir oturum açar ve geçmiş kaybolur.
# Belirti "sohbet aniden gitti" oluyor ama sebebi kodda görünmüyor, çünkü hata
# fırlatılmıyor. Bu iz, bir sonraki olayda tahmin etmek yerine bakabilmek için:
# her script koşusunda oturum kimliğini ve mesaj sayısını yazıyor. Kimlik
# değişip mesaj sayısı sıfırlanmışsa oturum yenilenmiş demektir.
def _session_log() -> None:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        ctx = get_script_run_ctx()
        sid = (ctx.session_id if ctx else "?")[:8]
        n = len(st.session_state.get("messages", []))
        Path("data/session.log").open("a").write(
            f"{time.strftime('%H:%M:%S')}  oturum={sid}  mesaj={n}\n"
        )
    except Exception:
        pass      # teşhis, uygulamayı asla düşürmemeli


# Sohbet artık SQLite'ta. `session_state` yalnızca "hangi sohbet açık" ve
# o sohbetin belleğe alınmış hâlini tutuyor; bağlantı düşüp oturum yenilense
# bile veri duruyor (bu bugün yaşandı ve sohbet kaybolmuştu).
if "conversation_id" not in st.session_state:
    recent = conversations.list_all(limit=1)
    st.session_state.conversation_id = recent[0]["id"] if recent else conversations.create()

if "messages" not in st.session_state:
    st.session_state.messages = conversations.load(st.session_state.conversation_id)

# Yanıt kipi BURADA ilkleniyor, model hapının yanında DEĞİL. Hap dosyanın
# sonunda çiziliyor; oysa yeniden üretim dalları ondan önce çalışıp
# `run_query` çağırıyor ve `answer_mode`'u okuyor. İlklemeyi widget'ın yanında
# bırakmak, "önce hangi dal koştu" sorusuna bağlı gizli bir bağımlılık
# yaratıyordu — quiz'i yeni soru sayısıyla üretirken AttributeError olarak
# patladı.
DEFAULT_MODE = "writer"
st.session_state.setdefault("answer_mode", DEFAULT_MODE)

_session_log()


def _open_conversation(conversation_id: int) -> None:
    # Odak sohbete ait bir karar: başka sohbete geçince taşınmamalı, yoksa
    # kullanıcı fark etmeden yalnızca bir belgede aramaya devam eder.
    st.session_state.pop("focus_doc", None)
    st.session_state.conversation_id = conversation_id
    st.session_state.messages = conversations.load(conversation_id)


def _new_conversation() -> None:
    # Boş bir sohbet açıkken yenisini açmak gereksiz kayıt üretir; mevcut
    # boş sohbeti yeniden kullan.
    st.session_state.pop("focus_doc", None)
    if not st.session_state.messages:
        return
    st.session_state.conversation_id = conversations.create()
    st.session_state.messages = []
# Arayüz metinleri Türkçe sabit. Yanıt dili kullanıcıya SORULMUYOR: sorunun
# dili zaten sınıflandırıcı tarafından tespit ediliyor ve cevap o dilde
# yazılıyor. Ayrıca bir seçici koymak, kullanıcıyı sistemin kendi başına
# doğru yaptığı bir kararı elle vermeye zorluyordu.
AVATAR = {
    # Emoji/karikatür simge yerine tasarım diline ait işaretler.
    # Kullanıcı: nötr bir halka — "soran taraf" için yeterli, dikkat çalmıyor.
    # Asistan: ürünün kendi markası — cevabı konuşan şey ürün.
    "user": Path(__file__).parent / "assets" / "avatar-user.png",
    "assistant": Path(__file__).parent / "assets" / "avatar-assistant.png",
}


def _avatar(rol: str):
    """Rol için avatar yolu; dosya yoksa Streamlit varsayılanına düşülüyor."""
    yol = AVATAR.get(rol)
    return str(yol) if yol and yol.exists() else None


def _logo_isareti() -> str:
    """Başlıktaki marka işareti: logo varsa o, yoksa eski elmas."""
    uri = _logo_uri()
    if not uri:
        return "<span class='mark'>◈</span>"
    return f"<img class='mark-logo' src='{uri}' alt='Orion Innovation'>"


@st.cache_resource
def _logo_uri() -> str:
    """Logoyu gömülü veri URI'si olarak döner.

    Ayrı dosya olarak servis etmek Streamlit'te statik dizin ayarı istiyor;
    10 KB'lık bir işaret için o karmaşıklığa değmez.
    """
    import base64

    yol = Path(__file__).parent / "assets" / "orion-logo.png"
    if not yol.exists():
        return ""
    veri = base64.b64encode(yol.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{veri}"


def _conversation_lang(default: str = "tr") -> str:
    """Arayüz dili SOHBETİN dilinden geliyor, ayarlardan değil.

    Sınıflandırıcı her soruda dili zaten algılıyor ve sonuca yazıyor. Ayrı
    bir dil seçicisi koymak kullanıcıya aynı bilgiyi iki kez sordurur:
    İngilizce soru soran biri arayüzün de İngilizce olmasını bekler, bunu
    ayrıca söylemesi gerekmemeli.

    Son cevabın dili esas alınıyor; sohbet henüz boşsa varsayılan kalıyor.
    """
    for message in reversed(st.session_state.get("messages", [])):
        lang = (message.get("result") or {}).get("lang")
        if lang in T:
            return lang
    return default


# SAYFA ÇATISI HEP TÜRKÇE, YALNIZCA SOHBET ALANI İKİ DİLLİ.
#
# `t`  — kenar çubuğu, başlık, indeks sayaçları, kullanım verileri. Bunlar
#        uygulamanın kendi arayüzü; sohbette İngilizce konuşuldu diye
#        "Bilgi tabanı" başlığının da değişmesi kullanıcıyı şaşırtıyor.
# `tc` — sohbet alanı: rozetler, kaynaklar, çalışma izi, eylem sırası, quiz
#        paneli, giriş kutusu. Bunlar cevabın parçası, cevabın dilinde olmalı.
t = T["tr"]
tc = T[_conversation_lang()]
lang_idx = 0


_AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
          "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def _tarih(utc_metin: str) -> str:
    """SQLite `datetime('now')` UTC yazıyor; kullanıcıya yerel saatle."""
    from datetime import datetime, timezone

    try:
        d = datetime.strptime(utc_metin, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone()
    except (TypeError, ValueError):
        return utc_metin or ""
    return f"{d.day} {_AYLAR[d.month - 1]} {d.year} · {d:%H:%M}"


def _tur(ozet: str | None) -> str:
    """Saklı özetin ilk satırındaki "Tür: …" bilgisi."""
    ilk = (ozet or "").split("\n", 1)[0]
    return ilk.split(":", 1)[1].strip() if ilk.startswith("Tür:") else ""


def buyuk_harf(metin: str, dil: str = "tr") -> str:
    """Dile göre büyük harf.

    Python'un `str.upper()` işlevi Türkçeyi bilmiyor: "Kibar ret" -> "KIBAR
    RET" (doğrusu "KİBAR RET"). Network Asistanı'nın rozetlerinde bu hata
    aylarca göründü. Türkçede önce i/ı eşlemesi, sonra büyük harf.
    """
    if dil == "tr":
        metin = metin.replace("i", "İ").replace("ı", "I")
    return metin.upper()


def label(mapping, key) -> str:
    return mapping.get(key, ("?", "?"))[lang_idx]


# --- kenar çubuğu ----------------------------------------------------------

with st.sidebar:
    # --- belgeler ----------------------------------------------------------
    st.markdown(
        "<div style='font-family:var(--mono);font-size:.63rem;letter-spacing:.1em;"
        "text-transform:uppercase;color:var(--signal);margin:.2rem 0 .6rem'>"
        f"{t['documents']}</div>",
        unsafe_allow_html=True,
    )

    # YÜKLEYİCİ HER İŞLEMDEN SONRA SIFIRLANIYOR. Streamlit her etkileşimde
    # betiği baştan koşuyor ve yükleyici dosyaları elinde tutuyor; sıfırlanmasa
    # her tıklamada aynı PDF yeniden işlenirdi (içerik özeti tekrarı yakalasa
    # bile her seferinde PDF açılıp sayfa sayılırdı). Anahtarı değiştirmek
    # bileşeni boşaltmanın tek güvenilir yolu.
    st.session_state.setdefault("uploader_nonce", 0)
    dosyalar = st.file_uploader(
        t["upload"], type=["pdf"], accept_multiple_files=True,
        label_visibility="collapsed", key=f"uploader_{st.session_state.uploader_nonce}",
    )
    if dosyalar:
        yeni_mesajlar = []
        for dosya in dosyalar:
            with st.status(f"{dosya.name} okunuyor…", expanded=False) as durum:
                AŞAMA = {"ocr": "taranmış sayfa okunuyor", "ozet": "özet çıkarılıyor",
                         "indeks": "indeksleniyor"}

                def ilerleme(asama: str, i: int, n: int, _d=durum, _ad=dosya.name) -> None:
                    ek = f" {i}/{n}" if n > 1 else ""
                    _d.update(label=f"{_ad} — {AŞAMA.get(asama, asama)}{ek}")

                sonuc = ingest.yukle(dosya.getvalue(), dosya.name, on_progress=ilerleme)
                if sonuc.status == "ready":
                    durum.update(label=f"{sonuc.title} — hazır", state="complete")
                    yeni_mesajlar.append(sonuc)
                elif sonuc.status == "duplicate":
                    durum.update(label=f"{sonuc.title} — zaten yüklü", state="complete")
                else:
                    durum.update(label=f"{dosya.name} — okunamadı", state="error")
                    st.error(sonuc.error)

        for sonuc in yeni_mesajlar:
            # Özet sohbete asistan mesajı olarak düşüyor: kullanıcı belgeyi
            # attığında "okudum, içinde şunlar var" cevabını görmeli. Geçmişe de
            # giriyor, böylece "bu belgede ceza ne kadar?" takip sorusu
            # "bu belge"nin hangisi olduğunu çözebiliyor.
            ocr_notu = f", {sonuc.ocr_pages} {t['ocr_pages']}" if sonuc.ocr_pages else ""
            icerik = (
                f"**{sonuc.title}** {t['uploaded']} — {sonuc.page_count} {t['pages']}{ocr_notu}."
                f"\n\n{sonuc.summary}"
            )
            mesaj = {
                "role": "assistant",
                "content": icerik,
                "result": {
                    "kind": "yukleme", "lang": "tr", "document_ids": [sonuc.document_id],
                    "page_count": sonuc.page_count, "ocr_pages": sonuc.ocr_pages,
                    "chunks": sonuc.chunks, "total_ms": sonuc.seconds * 1000,
                    "warnings": sonuc.warnings,
                },
            }
            st.session_state.messages.append(mesaj)
            conversations.append(st.session_state.conversation_id, mesaj)
        st.session_state.uploader_nonce += 1
        st.rerun()

    belgeler = ingest.listele()
    if not belgeler:
        st.caption(t["no_documents"])
    for belge in belgeler:
        baslik = belge["title"] or belge["filename"]
        durum_isareti = {"ready": "", "processing": "… ", "failed": "! "}.get(belge["status"], "")

        # Silme onay istiyor: geri alınamaz ve belgenin metni diskten de
        # siliniyor. Onay satırı KENDİ kabında — sohbet listesinde öğrenildi:
        # satır stilleri (görünmez ikon kutusu) buraya sızarsa "Vazgeç" kayboluyor.
        if st.session_state.get("confirm_doc") == belge["id"]:
            with st.container(key=f"docconfirm_{belge['id']}"):
                st.markdown(
                    f"<div class='conv-confirm'>“{html.escape(baslik)}” silinsin mi?</div>",
                    unsafe_allow_html=True,
                )
                evet, hayir = st.columns(2)
                if evet.button("Sil", key=f"docyes_{belge['id']}", type="primary",
                               use_container_width=True):
                    ingest.sil(belge["id"])
                    st.session_state.pop("confirm_doc", None)
                    if st.session_state.get("focus_doc") == belge["id"]:
                        st.session_state.pop("focus_doc", None)
                    st.rerun()
                if hayir.button("Vazgeç", key=f"docno_{belge['id']}", use_container_width=True):
                    st.session_state.pop("confirm_doc", None)
                    st.rerun()
            continue

        # SATIR YALNIZCA AD; TIKLANINCA BİLGİ KARTI AÇILIYOR. Özet kartta yok:
        # sohbette yükleme mesajı olarak duruyor ve "şu belgeyi özetle" diye
        # sorulabiliyor, kenar çubuğunda tekrarı listeyi uzatıyordu.
        secili = belge["id"] == st.session_state.get("doc_open")
        odakli = belge["id"] == st.session_state.get("focus_doc")
        with st.container(key=f"docrow_{belge['id']}"):
            col_ad, col_sil = st.columns([6, 1], gap="small")
            isaret = "◉ " if odakli else ""
            ipucu = (belge["error"] or None) if belge["status"] == "failed" else baslik
            if col_ad.button(f"{isaret}{durum_isareti}{baslik}", key=f"docopen_{belge['id']}",
                             use_container_width=True, help=ipucu):
                st.session_state.doc_open = None if secili else belge["id"]
                st.rerun()
            if col_sil.button("", icon=":material/delete:", key=f"docdel_{belge['id']}",
                              help=t["delete_doc"]):
                st.session_state.confirm_doc = belge["id"]
                st.rerun()

        if secili:
            with st.container(key=f"docinfo_{belge['id']}"):
                satirlar = [(t["doc_uploaded"], _tarih(belge["added_at"]))]
                if _tur(belge["summary"]):
                    satirlar.append((t["doc_type"], _tur(belge["summary"])))
                sayfa = f"{belge['page_count']}"
                if belge["ocr_pages"]:
                    sayfa += f" · {belge['ocr_pages']} {t['ocr_pages']}"
                satirlar += [(t["doc_pages"], sayfa), (t["doc_file"], belge["filename"])]
                st.markdown(
                    "<div class='doc-info'>" + "".join(
                        f"<div><span>{html.escape(k)}</span>{html.escape(v)}</div>"
                        for k, v in satirlar
                    ) + "</div>",
                    unsafe_allow_html=True,
                )
                if belge["status"] == "failed":
                    st.caption(f":material/error: {belge['error'] or 'işlenemedi'}")
                elif odakli:
                    if st.button(t["focus_clear"], key=f"focusoff_{belge['id']}",
                                 icon=":material/close:", use_container_width=True):
                        st.session_state.pop("focus_doc", None)
                        st.rerun()
                elif st.button(t["focus_ask"], key=f"focuson_{belge['id']}",
                               icon=":material/chat:", use_container_width=True):
                    # Odak sohbete çekiliyor: sonraki sorular yalnızca bu belgede
                    # aranıyor. Kart kapanıyor — işaret artık sohbet kutusunun
                    # üstünde duruyor, orada görünmesi yeterli.
                    st.session_state.focus_doc = belge["id"]
                    st.session_state.doc_open = None
                    st.rerun()

    st.divider()

    # --- sohbetler ---------------------------------------------------------
    # Kendi kabında: aşağıdaki sohbet satırlarıyla aynı sessiz görünümü
    # alması gerekiyor, ama kural listeye sızmamalı.
    with st.container(key="newchat"):
        clicked_new = st.button("Yeni sohbet", icon=":material/add:", use_container_width=True)
    if clicked_new:
        _new_conversation()
        st.rerun()

    items = conversations.list_all()
    if items:
        st.markdown(
            "<div style='font-family:var(--mono);font-size:.63rem;letter-spacing:.1em;"
            "text-transform:uppercase;color:var(--signal);margin:1rem 0 .5rem'>"
            "Sohbetler</div>",
            unsafe_allow_html=True,
        )
        active = st.session_state.conversation_id
        pending_delete = st.session_state.get("confirm_delete")

        for item in items:
            # SİLME ONAY İSTİYOR. Tek tıklamayla silme, geri alınamaz bir işlem
            # için kaza davetiyesi — kullanıcı başka bir sohbete geçmek isterken
            # yanlış ikona basabilir. Onay satırı, silinecek sohbetin ADINI da
            # gösteriyor: "hangisini siliyorum" sorusu ortada kalmasın.
            if pending_delete == item["id"]:
                # Onay satırı KENDİ kabında: aşağıdaki liste satırı stilleri
                # (32px görünmez ikon kutusu) buraya sızarsa "Vazgeç" kaybolur
                # — nitekim ilk denemede öyle oldu.
                with st.container(key=f"confirm_{item['id']}"):
                    st.markdown(
                        f"<div class='conv-confirm'>“{html.escape(item['title'])}” "
                        f"silinsin mi?</div>",
                        unsafe_allow_html=True,
                    )
                    yes, no = st.columns(2)
                    if yes.button("Sil", key=f"yes_{item['id']}", type="primary",
                                  use_container_width=True):
                        conversations.delete(item["id"])
                        st.session_state.pop("confirm_delete", None)
                        if item["id"] == active:
                            rest = conversations.list_all(limit=1)
                            if rest:
                                _open_conversation(rest[0]["id"])
                            else:
                                st.session_state.conversation_id = conversations.create()
                                st.session_state.messages = []
                        st.rerun()
                    if no.button("Vazgeç", key=f"no_{item['id']}",
                                 use_container_width=True):
                        st.session_state.pop("confirm_delete", None)
                        st.rerun()
                continue

            # Satır: başlık + silme ikonu. İkon her zaman DOM'da duruyor ama
            # yalnızca satırın üzerine gelince görünür oluyor (CSS) — liste
            # sakin kalıyor, eylem gerektiğinde ortaya çıkıyor.
            with st.container(key=f"convrow_{item['id']}"):
                col_title, col_del = st.columns([6, 1], gap="small")
                entry = ("▸ " if item["id"] == active else "") + item["title"]
                if col_title.button(entry, key=f"conv_{item['id']}",
                                    use_container_width=True):
                    _open_conversation(item["id"])
                    st.rerun()
                if col_del.button("", icon=":material/delete:",
                                  key=f"del_{item['id']}", help="Sohbeti sil"):
                    st.session_state.confirm_delete = item["id"]
                    st.rerun()

    st.divider()

    # Kullanım verileri KATLANMIŞ: bunlar sistemi geliştirirken gerekli,
    # soru soran kullanıcı için gürültü. Ana sayfada hiç görünmüyorlar;
    # burada da açmak isteyene açılıyor.
    summary = trace.summary()

    with st.expander(t["cost"]):
        col_a, col_b = st.columns(2)
        col_a.metric(t["queries"], summary["queries"])
        col_b.metric(t["avg"], f"${summary['avg_cost']:.4f}")
        st.caption(f"toplam  ${summary['total_cost']:.4f}")
        if summary["by_route"]:
            st.caption(" · ".join(f"{k} {v}" for k, v in summary["by_route"].items()))

    with st.expander(t["models"]):
        for tier_name, info in registry.describe().items():
            st.caption(
                f"**{tier_name}**  \n`{info['model'].split('/')[-1]}`  \n"
                f"<span style='opacity:.55'>{info['provider']} · "
                f"${info['price_in']} / ${info['price_out']} per 1M</span>",
                unsafe_allow_html=True,
            )




# --- sayfa dili ------------------------------------------------------------
#
# CSS `text-transform: uppercase` büyük harfe çevirirken SAYFANIN diline
# bakıyor ve Streamlit sayfayı `lang="en"` ile sunuyor. Sonuç: tablo
# başlığında "Güney Bilişim" -> "GÜNEY BILIŞIM" (doğrusu "BİLİŞİM"). Dil sohbetin
# dilinden alınıyor; İngilizce sohbette "FILE" -> "FİLE" hatası da çıkmasın diye.
#
# İframe KENAR ÇUBUĞUNDA: sıfır yükseklikli bir bileşen bile sohbet sütununda
# küçük bir boşluk bırakıyor.
with st.sidebar:
    components.html(
        f"<script>window.parent.document.documentElement.lang = "
        f"{_conversation_lang()!r};</script>",
        height=0,
    )


# --- başlık ----------------------------------------------------------------

st.markdown(
    f"""
    <div class='masthead'>
      <h1>{_logo_isareti()} {t['title']}</h1>
      <p>{t['subtitle']}</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# --- yardımcılar -----------------------------------------------------------


def render_badges(result: dict) -> None:
    """Kararların telemetrisi. Sıra bilinçli: önce YOL (cevap nereden geldi),
    sonra maliyet ve süre, en sonda yalnızca gerçekleşmişse istisnalar.

    Hepsi monospace ve küçük: bunlar modelin yazdığı metin değil, sistemin
    ölçtüğü veri — kullanıcı ikisini bir bakışta ayırabilmeli.

    Rozetler MESAJIN KENDİ diline uyuyor. Genel `lang_idx` Türkçeye sabitti ve
    etiket sözlüklerinin İngilizce yarısı hiç kullanılmıyordu; aynı satırda
    "genel kavram" ile "difficulty 1/5" yan yana çıkıyordu. Mesaj bazında
    bakmanın ikinci faydası: sohbette dil değişince geriye kaydırılan eski
    mesajların rozetleri de değişmiyor.
    """
    dil = result.get("lang") or _conversation_lang()
    tb = T.get(dil, T["tr"])

    if result.get("kind") == "yukleme":
        rozetler = [("badge-primary", "BELGE YÜKLENDİ"),
                    ("badge", f"{result.get('page_count', 0)} {tb['pages']}")]
        if result.get("ocr_pages"):
            rozetler.append(("badge-strong", f"{result['ocr_pages']} OCR"))
        rozetler.append(("badge", f"{(result.get('total_ms') or 0) / 1000:.1f}s"))
        satir = "".join(f"<span class='badge {c}'>{html.escape(x)}</span>" for c, x in rozetler)
        st.markdown(f"<div class='badge-row'>{satir}</div>", unsafe_allow_html=True)
        return
    idx = 0 if dil == "tr" else 1

    def etiket(mapping, key) -> str:
        return mapping.get(key, ("?", "?"))[idx]

    badges = []

    badges.append(("badge-primary", buyuk_harf(etiket(ROUTE_LABEL, Route(result["route"])), dil)))
    if result.get("scope_titles"):
        # Geriye kaydırıldığında hangi cevabın hangi belgeye sorulduğu
        # görünmeli; odak işareti yalnızca GÜNCEL durumu gösteriyor.
        ad = result["scope_titles"][0]
        kisa = ad if len(ad) <= 28 else ad[:27] + "…"
        badges.append(("badge-strong", f"{tb['focus_badge']}: {html.escape(kisa)}"))

    badges.append(("badge", etiket(CATEGORY_LABEL, Category(result["category"])).lower()))

    if result.get("full_context"):
        badges.append(("badge-strong", "tam okuma"))

    if result["model_used"]:
        # model adı uzun; sağlayıcı ön ekini atıp okunur kısmı bırak
        short = result["model_used"].split("/")[-1]
        cls = "badge-strong" if result["tier_used"] == "strong" else "badge"
        badges.append(
            (cls, f"{short}  ·  {tb['difficulty']} {result['difficulty']}/5")
        )

    badges.append(("badge", f"${result['cost_usd']:.4f}"))
    badges.append(("badge", f"{result['total_ms'] / 1000:.1f}s"))

    # Hız sınırı beklemesi ayrı gösteriliyor: bu süre modelin düşünmesi değil,
    # sağlayıcının dakikalık token kotasının dolması. Ayrılmadığında sistem
    # donmuş gibi görünüyor ve kullanıcı neyin yavaş olduğunu bilemiyor.
    waited = result.get("rate_limit_wait_s") or 0
    if waited:
        badges.append(("badge-warn", f"{waited:.0f}s {tb['rate_limited']}"))

    if result["rewrites"]:
        badges.append(("badge", f"↻ {result['rewrites']} {tb['rewrites']}"))
    if result["regens"]:
        badges.append(("badge", f"⟳ {result['regens']} {tb['regens']}"))
    if result["low_confidence"]:
        badges.append(("badge-warn", tb["low_conf"]))

    # Değişken adı `html` DEĞİL: fonksiyonun herhangi bir yerinde `html = …`
    # yazmak adı fonksiyonun TAMAMINDA yerel yapıyor ve yukarıdaki
    # `html.escape` çağrısı UnboundLocalError ile patlıyordu.
    satir = "".join(f"<span class='badge {c}'>{txt}</span>" for c, txt in badges)
    st.markdown(f"<div class='badge-row'>{satir}</div>", unsafe_allow_html=True)


@st.cache_data(show_spinner=False, max_entries=24)
def _sayfa_gorseli(document_id: int, page_no: int) -> bytes | None:
    """Belgenin bir sayfasının PNG görüntüsü.

    "Uydurmuyor" iddiasının en güçlü kanıtı, cevabın dayandığı sayfanın
    kendisi: okuyucu chunk metnine değil belgenin aslına bakabiliyor. Render
    pahalı (sayfa başına ~0.2 sn) ve aynı sayfa tekrar tekrar açılıyor, bu
    yüzden sonuç önbellekte tutuluyor.
    """
    import io

    from belge import pdf as pdfmod

    veri = ingest.pdf_bytes(document_id)
    if not veri:
        return None
    try:
        gorsel = pdfmod.render_page(veri, page_no, dpi=110)
    except Exception:
        return None
    tampon = io.BytesIO()
    gorsel.save(tampon, format="PNG")
    return tampon.getvalue()


def _sayfa_onizleme(document_id: int, sayfalar: list[int], anahtar: str) -> None:
    """Kaynak bloğunun altındaki "sayfayı göster" düğmeleri ve görüntü."""
    if not sayfalar:
        return
    acik = st.session_state.get("kaynak_sayfa")
    with st.container(key=f"sayfabtn_{anahtar}"):
        kolonlar = st.columns(min(len(sayfalar), 6))
        for kolon, no in zip(kolonlar, sayfalar[:6]):
            secili = acik == (document_id, no)
            if kolon.button(f"s. {no}", key=f"sayfa_{anahtar}_{no}",
                            icon=":material/description:",
                            help=tc["show_page"], use_container_width=True):
                st.session_state.kaynak_sayfa = None if secili else (document_id, no)
                st.rerun()
    if acik and acik[0] == document_id and acik[1] in sayfalar:
        png = _sayfa_gorseli(document_id, acik[1])
        if png:
            st.image(png, caption=f"{tc['page_word']} {acik[1]}", use_container_width=True)
        else:
            st.caption(tc["page_missing"])


def render_sources(result: dict, idx: int = 0) -> None:
    """Kaynakları belge başına gösterir, altında kullanılan bölümlerle.

    Atıf numarası belgeye ait; bir belgeden birden çok bölüm kullanıldıysa
    hepsi aynı numaranın altında, madde ve sayfa bilgisiyle listeleniyor.

    Bölüm metni de gösteriliyor: iddiayı doğrulamanın yolu modelin hangi
    cümleleri okuduğunu görmek. Taranmış belgede bu metin OCR çıktısı —
    kullanıcı "sistem ne okudu" sorusunun cevabını da burada görüyor.
    """
    citations = result.get("citations") or []
    if not citations:
        return

    tam = bool(result.get("full_context"))
    passage_count = sum(len(c.get("passages") or []) for c in citations)
    heading = f"{tc['sources']} — {len(citations)} {tc['document_word']}"
    if tam:
        heading += f", {tc['full_read']}"
    elif passage_count:
        heading += f", {passage_count} {tc['passage_word']}"

    with st.expander(heading):
        for idx, c in enumerate(citations):
            if idx:
                st.markdown(
                    "<div style='height:1px;background:var(--line);margin:1rem 0'></div>",
                    unsafe_allow_html=True,
                )
            meta = html.escape(c.get("filename") or "")
            if tam:
                # BELGENİN TAMAMI OKUNDUĞUNDA bölüm bölüm metin basmak (29 kutu)
                # kaynağı göstermiyor, belgeyi yeniden yazıyor. Söylenmesi
                # gereken tek şey "hangi sayfalar okundu".
                sayfalar = [p for x in c.get("passages") or []
                            for p in (x.get("page_start"), x.get("page_end")) if p]
                if sayfalar:
                    alt, ust = min(sayfalar), max(sayfalar)
                    aralik = f"s. {alt}" if alt == ust else f"s. {alt}–{ust}"
                    meta += f" · {tc['full_read']} · {aralik}"
            st.markdown(
                f"<span class='src-n'>[{c['n']}]</span>"
                f"<span class='src-title'>{html.escape(c['title'])}</span><br>"
                f"<span class='src-meta src-meta-line'>{meta}</span>",
                unsafe_allow_html=True,
            )
            sayfalar = sorted({p for x in c.get("passages") or []
                               for p in (x.get("page_start"), x.get("page_end")) if p})
            # Anahtar MESAJ indeksini taşıyor: aynı sohbette iki cevap aynı
            # belgeyi gösterdiğinde Streamlit çakışan anahtar hatası veriyor.
            _sayfa_onizleme(c["document_id"], sayfalar, f"{idx}_{c['n']}")
            if tam:
                continue
            for passage in c.get("passages") or []:
                text = (passage.get("text") or "").strip()
                if not text:
                    continue
                section = (passage.get("section") or "").strip()
                tail = "…" if passage.get("truncated") else ""
                # KAÇIŞ ZORUNLU. Chunk'lar PDF'ten çıkarılmış ham metin ve "<",
                # ">" gibi karakterler içerebiliyor; kaçışsız basıldığında
                # tarayıcı onları etiket sanıp metni YUTUYOR — Network
                # Asistanı'nda parçaların %3.9'unda ölçülmüştü.
                head = (
                    f"<span class='passage-head'>{html.escape(section)}</span>"
                    if section else ""
                )
                st.markdown(
                    f"<div class='passage'>{head}{html.escape(text)}{tail}</div>",
                    unsafe_allow_html=True,
                )


# Boru hattı düğümlerinin kullanıcıya görünen adları.
#
# Kodun iç adları (`hallucination_check`) burada işe yaramıyor:
# kullanıcı sistemin ne yaptığını anlamak için bakıyor, dosya adı öğrenmek için
# değil. Her satır bir CÜMLE: "ne yapıldı", "neyle sonuçlandı".
STEP_LABEL = {
    "classifier":          ("Soru çözümlendi",      "Soru türü, zorluk ve ilgili belgeler belirlendi"),
    "retrieve":            ("Belgelerde arandı",    "Belgelerin bölümleri tarandı"),
    "grade":               ("Bölümler puanlandı",   "Bulunanların soruyla ilgisi değerlendirildi"),
    "rewrite":             ("Arama yenilendi",      "Sonuç yetersizdi, sorgu belgenin diliyle yeniden yazıldı"),
    "model_router":        ("Model seçildi",        "Sorunun zorluğuna göre kademe belirlendi"),
    "generate":            ("Cevap yazıldı",        "Bulunan bölümlerden cevap üretildi"),
    "calculation_check":   ("Hesap denetimi",       "Cevaptaki hesaplar yazılımla yeniden yapıldı"),
    "hallucination_check": ("Kaynak doğrulaması",   "Her ifadenin belgede karşılığı var mı bakıldı"),
    "sufficiency_check":   ("Yeterlilik kontrolü",  "Cevap soruyu gerçekten karşılıyor mu bakıldı"),
    "refusal":             ("Kibar ret",            "Soru yüklenen belgelerle ilgili olmadığı için cevaplanmadı"),
    "summary":             ("Saklı özet",           "Yükleme sırasında çıkarılan özet getirildi, model çalışmadı"),
}

# Adımların yanında görünen teknik alanların Türkçe karşılıkları.
STEP_FIELD = {
    "query":          "arama sorgusu",
    "hits":           "bulunan bölüm",
    "relevant":       "ilgili bulunan",
    "tier":           "kademe",
    "context_tokens": "bağlam",
    "category":       "kategori",
    "difficulty":     "zorluk",
    "documents":      "adı geçen belge",
    "regen":          "yeniden üretim",
    "attempt":        "deneme",
    "checked":        "denetlenen hesap",
    "wrong":          "hatalı",
}

CATEGORY_PLAIN = {
    "belge_ici":    "tek belgeden cevaplanan soru",
    "capraz_belge": "belgeleri birlikte okumayı gerektiren soru",
    "belge_ozeti":  "belge özeti",
    "kapsam_disi":  "belgelerle ilgisiz",
}


def _step_detail(step: dict) -> str:
    """Adımın yanındaki teknik alanları okunur bir cümleye çevirir."""
    parts = []
    for key, value in step.items():
        if key in {"node", "ms", "cost_usd", "tokens"}:
            continue
        if key == "category":
            value = CATEGORY_PLAIN.get(str(value), value)
        elif key == "difficulty":
            value = f"{value}/5"
        elif key == "context_tokens":
            value = f"{value} token"
        elif key == "query":
            value = f"“{value}”"
        elif key == "regen" and not value:
            continue                    # sıfırıncı yeniden üretim bilgi değil
        parts.append(f"{STEP_FIELD.get(key, key)}: {value}")
    return "  ·  ".join(parts)


def render_trace(result: dict) -> None:
    """Sistemin ne yaptığını adım adım gösterir.

    Bu bölüm bir hata ayıklama çıktısı DEĞİL, kullanıcıya verilen bir hesap:
    "cevabı nereden buldun, neye ne kadar harcadın". Bu yüzden kodun iç
    adları (`hallucination_check`) yerine ne yapıldığını
    söyleyen cümleler kullanılıyor; sayılar da ikinci planda, sağda ve sönük.
    """
    steps = result.get("steps", [])
    total_ms = sum(s["ms"] for s in steps) or 1

    with st.expander(f"{tc['trace']} — {len(steps)} {tc['trace_steps']}"):
        rows = []
        for step in steps:
            name, explanation = STEP_LABEL.get(
                step["node"], (step["node"], "")
            )
            detail = _step_detail(step)
            seconds = step["ms"] / 1000
            # Payı gösteren ince çubuk: hangi adımın zamanı yediğini
            # okumadan görmek için.
            width = max(2, round(step["ms"] / total_ms * 100))
            cost = f"${step['cost_usd']:.4f}" if step["cost_usd"] else "—"

            rows.append(
                f"<div class='tstep'>"
                f"  <div class='tstep-head'>"
                f"    <span class='tstep-name'>{name}</span>"
                f"    <span class='tstep-num'>{seconds:.1f} sn · {cost}</span>"
                f"  </div>"
                f"  <div class='tbar'><i style='width:{width}%'></i></div>"
                f"  <div class='tstep-why'>{explanation}"
                + (f"<br><span class='tstep-detail'>{detail}</span>" if detail else "")
                + f"  </div>"
                f"</div>"
            )
        st.markdown("".join(rows), unsafe_allow_html=True)

        if result.get("warnings"):
            st.markdown(
                "<div class='tnotes'>" + "".join(
                    f"<div>› {html.escape(w)}</div>" for w in result["warnings"]
                ) + "</div>",
                unsafe_allow_html=True,
            )


def _copy_to_clipboard(text: str) -> bool:
    """Metni panoya kopyalar.

    Uygulama kullanıcının kendi makinesinde koştuğu için sunucu tarafı pano
    doğru panodur. Tarayıcı tarafı (`navigator.clipboard`) ayrı bir iframe
    gerektiriyor ve o iframe'in kendi stili oluyor — dört ikonun hizası
    bozuluyordu. Uzaktan servis edilirse bu yol çalışmaz; o durumda metin
    ekranda gösteriliyor ve kullanıcı elle kopyalıyor.
    """
    import platform
    import subprocess

    command = {
        "Darwin": ["pbcopy"],
        "Linux": ["xclip", "-selection", "clipboard"],
        "Windows": ["clip"],
    }.get(platform.system())
    if not command:
        return False
    try:
        subprocess.run(command, input=text.encode("utf-8"), check=True, timeout=5)
        return True
    except Exception:
        return False


def scroll_to_bottom() -> None:
    """Sohbeti en alta kaydırır.

    Streamlit her etkileşimde betiği baştan koşuyor ama kaydırma konumunu
    KORUYOR: kullanıcı yukarıdayken soru yazınca hem sorusu hem cevabı
    görünmeyen bir yere ekleniyordu.

    `st.markdown` içindeki `<script>` çalışmıyor (Streamlit temizliyor), o
    yüzden sıfır yükseklikli bir bileşen iframe'i kullanılıyor ve üst
    pencereye erişiliyor.

    Tek seferlik bir kaydırma yetmiyor: iframe, uzun cevap yerleşmeden önce
    yüklenebiliyor ve o anki `scrollHeight` gerçek yüksekliği vermiyor. Kısa
    bir süre boyunca tekrarlanıyor.
    """
    components.html(
        """
        <script>
          const doc = window.parent.document;
          function dibeIn() {
            const adaylar = [...doc.querySelectorAll('section, div')]
              .filter(el => el.scrollHeight > el.clientHeight + 40);
            if (!adaylar.length) return;
            // En derindeki kaydırılabilir kap gerçek sohbet kabı; en dıştaki
            // sayfanın kendisi olabiliyor.
            const el = adaylar[adaylar.length - 1];
            el.scrollTop = el.scrollHeight;
          }
          let n = 0;
          const t = setInterval(() => { dibeIn(); if (++n > 12) clearInterval(t); }, 80);
          dibeIn();
        </script>
        """,
        height=0,
    )


def render_actions(idx: int, message: dict) -> None:
    """Cevabın altındaki eylem sırası: beğen · beğenme · yeniden üret · kopyala.

    Rozetler gibi küçük ve sessiz duruyorlar — cevabın kendisiyle yarışmamalı.
    """
    result = message.get("result") or {}
    answer = message.get("content") or ""
    voted = message.get("voted")
    # Oy önbelleğe değil sınıflandırıcının örnek havuzuna gidiyor; kaynağı
    # olan her gerçek cevap oylanabilir. Ret ve özet yollarında oylanacak bir
    # sınıflandırma kararı dışında bir şey yok, onlar da oylanabilir.
    can_vote = bool(result.get("query"))

    with st.container(key=f"actionrow_{idx}"):
        cols = st.columns([1, 1, 1, 1, 6])

    def vote(direction: int, label_text: str) -> None:
        feedback.record(
            result.get("query", ""),
            vote=direction,
            lang=result.get("lang", "tr"),
            predicted_category=result.get("category"),
        )
        st.session_state.messages[idx]["voted"] = label_text
        conversations.set_vote(st.session_state.conversation_id, idx, label_text)
        st.rerun()

    # İKONLAR MATERIAL SYMBOLS, EMOJİ DEĞİL. Emoji her platformda farklı
    # çiziliyor, renkli geliyor ve çizgi kalınlığı diğer ikonlarla uyuşmuyordu
    # (👍👎 dolu ve renkli, ↻⧉ ince ve tek renk). Material ailesi tek çizgi
    # ağırlığında ve metin rengini alıyor, yani arayüzün geri kalanıyla
    # tutarlı. `help` metni aynı zamanda erişilebilir ad görevi görüyor —
    # yalnızca ikon taşıyan bir butonun okuyucuya ne söylediği bu.
    up_icon = ":material/thumb_up:" if voted != tc["thanks_up"] else ":material/thumb_up_filled:"
    down_icon = ":material/thumb_down:" if voted != tc["thanks_down"] else ":material/thumb_down_filled:"

    if cols[0].button("", icon=up_icon, key=f"up_{idx}", help=tc["helpful_up"],
                      disabled=not can_vote or bool(voted)):
        vote(1, tc["thanks_up"])

    if cols[1].button("", icon=down_icon, key=f"down_{idx}", help=tc["helpful_down"],
                      disabled=not can_vote or bool(voted)):
        vote(-1, tc["thanks_down"])

    # Yeniden üretim yalnızca SON cevap için: ortadaki bir cevabı değiştirmek
    # sonraki turların dayandığı zemini kaydırır ve sohbet tutarsızlaşır.
    is_last = idx == len(st.session_state.messages) - 1
    if cols[2].button("", icon=":material/refresh:", key=f"regen_{idx}",
                      help=tc["regen"], disabled=not is_last):
        st.session_state.regenerate = idx
        st.rerun()

    if cols[3].button("", icon=":material/content_copy:", key=f"copy_{idx}",
                      help=tc["copy"]):
        if _copy_to_clipboard(answer):
            st.session_state[f"copied_{idx}"] = True
        else:
            st.session_state[f"copyfail_{idx}"] = True
        st.rerun()

    if st.session_state.pop(f"copied_{idx}", None):
        st.caption(tc["copied"])
    if st.session_state.get(f"copyfail_{idx}"):
        st.caption(tc["copy_manual"])
        st.code(answer, language=None)

    if voted:
        st.caption(voted)


# --- boş durum -------------------------------------------------------------

# KUTU BİR KABIN İÇİNDE. Streamlit betiği yukarıdan aşağı koşuyor: ilk soru
# yazıldığında mesaj listesi HENÜZ boş olduğu için kutu çiziliyor, sonra aynı
# koşuda kullanıcının sorusu ve cevap onun ALTINA ekleniyor. Sonuç, "Ne
# sorabilirsiniz" kutusuna bitişik bir sohbet ve cevap gelene kadar (10-80 sn)
# ekranda duran ölü bir kutu. Kap, soru geldiğinde aşağıda boşaltılıyor.
bos_kutu = st.empty()

if not st.session_state.messages:
    with bos_kutu.container():
        if not _warm["done"]:
            st.caption(
                f"Arama motoru hazırlanıyor ({time.time() - _warm['started']:.0f} sn) — "
                "şimdi belge yükleyebilir ya da soru yazabilirsiniz"
            )
        elif _warm["error"]:
            st.caption(f":material/warning: ısıtma başarısız: {_warm['error']}")

        hazir = [b for b in ingest.listele() if b["status"] == "ready"]
        if hazir:
            durum_cumlesi = (
                f"{len(hazir)} belge yüklü. Sorunuz bu belgelerin içinde aranır; cevap "
                "yalnızca bulunan bölümlere dayanır ve sayfa numarasıyla verilir."
            )
        else:
            durum_cumlesi = (
                "Başlamak için sol panelden bir PDF yükleyin. Dijital PDF'ler ve "
                "taranmış belgeler okunur; yükleme bitince belgenin özetini çıkarırım."
            )
        st.markdown(
            f"""
            <div class='empty'>
              <h4>Ne sorabilirsiniz</h4>
              <p>{durum_cumlesi}</p>
              <div>
                <span class='chip'><b>›</b> Bu sözleşmede gecikme cezası ne kadar?</span>
                <span class='chip'><b>›</b> Belgeleri özetle</span>
                <span class='chip'><b>›</b> Hangi sözleşme yönetmeliğe aykırı?</span>
                <span class='chip'><b>›</b> Teslim süreleri sözleşmelerde nasıl farklılaşıyor?</span>
              </div>
              <div class='empty-note'>Belgede olmayan bir şeyi sorarsanız sistem bunu söyler, uydurmaz.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# --- sohbet geçmişi --------------------------------------------------------

for idx, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"], avatar=_avatar(message["role"])):
        if message["role"] == "user":
            st.markdown(message["content"])
            continue

        result = message.get("result") or {}
        render_badges(result)
        st.markdown(message["content"] or tc["no_answer"])
        if result.get("kind") == "yukleme":
            # Yükleme mesajında eylem sırası yok: oylanacak bir cevap değil, ve
            # "yeniden üret" burada bir ÖNCEKİ sorunun cevabını silip yeniden
            # yazardı.
            for uyari in result.get("warnings") or []:
                st.caption(f":material/warning: {uyari}")
            continue
        render_sources(result, idx)
        render_trace(result)
        render_actions(idx, message)


# --- yeni sorgu ------------------------------------------------------------

def run_query(question: str, *, history: list[dict], regenerate: bool = False):
    """Sorguyu akıtarak çalıştırır ve (cevap, telemetri) döner.

    Hem yeni soru hem "yeniden üret" buradan geçiyor: iki ayrı kopya olsaydı
    biri güncellenip diğeri unutulurdu.

    AKIŞ NEDEN AYRI BİR İŞ PARÇACIĞINDA
    Sağlayıcı metni düzensiz aralıklarla gönderiyor: ölçümde 544 token yalnızca
    15 parça hâlinde geldi (parça başına ~36 token) ve yüksek gecikmeli bir
    bağlantı bunları TCP seviyesinde daha da topluyor. Parçaları geldikleri anda
    basınca ekran sıçraya sıçraya yazıyor. Bu yüzden ÜRETİM ile GÖSTERİM ayrıldı:
    boru hattı arka planda koşup gelen metni kuyruğa bırakıyor, ana iş parçacığı
    o kuyruktan sabit hızda okuyup yazıyor.

    Arka plan iş parçacığı hiçbir `st.*` çağrısı YAPMIYOR (yalnızca kuyruğa
    yazıyor); script bağlamı gerektiren API'ler ana iş parçacığında kalıyor.
    """
    placeholder = st.empty()

    # HIZ / TRAFİK DENGESİ
    # Her çerçeve, büyüyen metnin TAMAMINI websocket üzerinden yeniden
    # gönderiyor — Streamlit kısmi güncelleme yapamıyor. 30 fps'te 2.000
    # karakterlik bir cevap ~250 tam gönderim demek; yavaş bir bağlantıda bu
    # websocket'i tıkayabiliyor. 15 fps göz için hâlâ akıcı (sinema 24 fps)
    # ama trafiği yarıya indiriyor.
    FPS, DRAIN_SECONDS, MIN_DELTA = 15, 0.45, 4
    FRAME = 1.0 / FPS

    chunks: "queue.Queue[str]" = queue.Queue()
    steps: "queue.Queue[str]" = queue.Queue()
    finished = threading.Event()
    outcome: dict = {}

    # SESSION_STATE ANA İŞ PARÇACIĞINDA OKUNUYOR.
    #
    # Aşağıdaki `worker` ayrı bir iş parçacığında koşuyor ve orada Streamlit
    # bağlamı yok ("missing ScriptRunContext"). O bağlamda `st.session_state`
    # boş bir nesne oluyor: öznitelikle okumak AttributeError atıyor, `.get()`
    # ise sessizce varsayılana düşüp kullanıcının model seçimini yok sayıyor —
    # ikincisi daha kötü, çünkü fark edilmiyor.
    #
    # Değer bu yüzden BURADA, iş parçacığı başlamadan önce okunup kapanışla
    # taşınıyor.
    mode = st.session_state.get("answer_mode", DEFAULT_MODE)
    # Odak da aynı gerekçeyle burada okunuyor.
    odak = st.session_state.get("focus_doc")

    def worker() -> None:
        try:
            outcome["state"] = graph.run(
                question, on_delta=chunks.put,
                history=history, regenerate=regenerate, on_step=steps.put,
                force_tier=mode, scope_document_ids=[odak] if odak else None,
            )
        except Exception as exc:          # sunum katmanı çökmemeli
            outcome["error"] = exc
        finally:
            finished.set()

    threading.Thread(target=worker, daemon=True).start()

    # İLERLEME GÖSTERİMİ. "Kaynaklar taranıyor…" tek bir belirsiz mesajdı ve
    # sorgular 10-80 saniye sürebiliyor; o süre boyunca ne olduğunu bilmemek
    # sistemin donduğu izlenimi veriyordu. Artık boru hattı her düğüme
    # girerken haber veriyor ve burada sırayla gösteriliyor: tamamlananlar
    # sönük, o an çalışan vurgulu.
    status = st.empty()
    done_steps: list[str] = []
    current: str | None = None
    started_at = time.time()

    def render_status() -> None:
        rows = []
        for name in done_steps:
            label, _ = STEP_LABEL.get(name, (name, ""))
            rows.append(f"<div class='pstep done'>✓ {label}</div>")
        if current:
            label, why = STEP_LABEL.get(current, (current, ""))
            rows.append(
                f"<div class='pstep live'><span class='pdot'></span>{label}"
                f"<span class='pwhy'>{why}</span></div>"
            )
        elapsed = time.time() - started_at
        rows.append(f"<div class='pelapsed'>{elapsed:.0f} sn</div>")
        try:
            status.markdown("".join(rows), unsafe_allow_html=True)
        except Exception:
            pass

    shown, pending = "", ""
    while True:
        try:
            while True:
                name = steps.get_nowait()
                if current:
                    done_steps.append(current)
                current = name
        except queue.Empty:
            pass

        try:
            while True:
                pending += chunks.get_nowait()
        except queue.Empty:
            pass

        # Metin akmaya başladığında ilerleme listesi kalkıyor: cevabın kendisi
        # zaten en iyi ilerleme göstergesi.
        if pending or shown:
            status.empty()
        else:
            render_status()

        if pending:
            # Birikmiş metni sabit sürede tüketecek kadar karakter bırak:
            # kuyruk şişerse hızlanır, boşalırsa yavaşlar — ama her çerçevede
            # bir şeyler yazılır, yani duraklama olmaz.
            step = max(MIN_DELTA, int(len(pending) * FRAME / DRAIN_SECONDS))
            shown, pending = shown + pending[:step], pending[step:]
            try:
                # Akan metin markdown olarak yorumlanmalı ama ham HTML olarak
                # DEĞİL: cevapta "< 5%" geçtiğinde tarayıcı onu etiket sanıp
                # gerisini yutuyordu.
                placeholder.markdown(shown + " ▌")
            except Exception:
                pass      # tek bir çizim hatası cevabı düşürmemeli


        if finished.is_set() and chunks.empty() and not pending:
            break
        time.sleep(FRAME)

    if "error" in outcome:
        placeholder.empty()
        st.error(f"{type(outcome['error']).__name__}: {outcome['error']}")
        st.stop()

    # Akışta gösterilen taslak doğrulama düğümlerinden GEÇMEMİŞ hâliydi;
    # yeniden üretim olduysa nihai metin farklı. Her durumda kesinleşmiş
    # cevapla değiştiriliyor.
    placeholder.empty()
    state = outcome["state"]
    return state.answer, state.to_dict()


def _history_for(upto: int) -> list[dict]:
    """`upto` indeksinden ÖNCEKİ turlar — takip sorularını çözmek için.

    Yükleme mesajları da giriyor: "bu belgede ceza ne kadar?" sorusundaki "bu
    belge", sohbette az önce yüklenen belgedir ve sınıflandırıcı bunu ancak
    yükleme mesajını görürse çözebilir.
    """
    return [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:upto]
        if m.get("content")
    ]


# --- yeniden üretim isteği -------------------------------------------------

if st.session_state.pop("regenerate", None) is not None:
    # Son asistan mesajı ve onu doğuran soru bulunuyor.
    msgs = st.session_state.messages
    last_user = next(
        (i for i in range(len(msgs) - 1, -1, -1) if msgs[i]["role"] == "user"), None
    )
    if last_user is not None:
        question = msgs[last_user]["content"]
        with st.chat_message("assistant", avatar=_avatar("assistant")):
            answer, result = run_query(
                question, history=_history_for(last_user), regenerate=True
            )
        # Eski cevap yerini yenisine bırakıyor: iki cevabı üst üste göstermek
        # hangisinin geçerli olduğunu belirsizleştirirdi.
        st.session_state.messages = msgs[: last_user + 1] + [
            {"role": "assistant", "content": answer, "result": result}
        ]
        conversations.replace_after(
            st.session_state.conversation_id, last_user,
            {"role": "assistant", "content": answer, "result": result},
        )
        st.rerun()


# --- model seçici ----------------------------------------------------------
#
# KONUM: sohbet girişinin sağ üstünde, ona yapışık. Kenar çubuğunda dururken
# kimse görmüyordu — soru yazarken göz orada değil.
#
# `position: sticky` kullanılıyor, `fixed` DEĞİL: sabit konumlandırma öğeyi
# görüntü alanına bağlıyor ve kenar çubuğu açıldığında hizası kayıyor (bugün
# soru kutusunda bir kez yaşandı). Sticky normal akışta kalıp ana sütunun
# genişliğini takip ediyor.
#
# "Otomatik" seçeneği YOK: kullanıcı modeli adıyla seçiyor. Yönlendirici kodu
# duruyor ve `force_tier` verilmediğinde hâlâ çalışıyor (bench ve testler onu
# kullanıyor), ama arayüzde karar kullanıcının.


def _model_label(model_id: str) -> str:
    """`gemini-3.5-flash-lite` -> `3.5 Flash-Lite`. Sağlayıcı ön eki ve marka
    adı düşüyor; kullanıcı sürümü ve sınıfı tanıyor, tam kimliği değil."""
    name = (model_id or "").split("/")[-1]
    for prefix in ("gemini-", "claude-", "gpt-", "meta-llama-"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return " ".join(w.capitalize() if not w[0].isdigit() else w
                    for w in name.replace("_", "-").split("-"))


MODE_HINT = {
    "writer": "En hızlı yanıtlar",
    "strong": "Karmaşık sorunları çözme",
}
# Etiket AMACI söylüyor, model adını değil. Ücretsiz kurulumda iki kademe de
# aynı modele bağlı; adı öne koyunca menüde birebir aynı iki satır çıkıyordu
# ve seçim anlamsız görünüyordu. Model adı açıklamada, ikincil bilgi olarak.
MODES = {
    "writer": ("Hızlı", f"{_model_label(config.get('tiers.writer.model', ''))} · {MODE_HINT['writer']}"),
    "strong": ("Derin analiz", f"{_model_label(config.get('tiers.strong.model', ''))} · {MODE_HINT['strong']}"),
}
_active = st.session_state.get("answer_mode", DEFAULT_MODE)

# Hap ve giriş kutusu AYNI sabit çubukta. Hap normal akıştayken sayfa
# kaydırıldığında yukarı gidiyordu; `st.bottom`, Streamlit'in sohbet girişini
# tuttuğu kabın ta kendisi, dolayısıyla ikisi birlikte hareket ediyor ve
# kenar çubuğu açılıp kapandığında hizaları da birlikte kayıyor.
# Odaklanılan belge hâlâ duruyor mu (başka sekmede silinmiş olabilir)?
_odak_belge = next(
    (b for b in ingest.listele()
     if b["id"] == st.session_state.get("focus_doc") and b["status"] == "ready"),
    None,
)
if st.session_state.get("focus_doc") and _odak_belge is None:
    st.session_state.pop("focus_doc", None)

with st.bottom:
    col_odak, col_mod = st.columns([3, 1], vertical_alignment="center")
    if _odak_belge:
        with col_odak, st.container(key="focuschip"):
            _ad = _odak_belge["title"] or _odak_belge["filename"]
            _kisa = _ad if len(_ad) <= 42 else _ad[:41] + "…"
            # İkon Material, emoji değil; kapatma işareti sağda — çipin
            # "kaldırılabilir" olduğunu okunduğu yerde söylüyor.
            if st.button(f"{tc['focus_only']}: {_kisa}", key="focus_chip",
                         icon=":material/close:", icon_position="right",
                         help=tc["focus_clear"]):
                st.session_state.pop("focus_doc", None)
                st.rerun()
    with col_mod, st.container(key="modepill"):
        with st.popover(MODES[_active][0], use_container_width=False):
            # HER SEÇENEK TEK BİR BUTON. Başlık ve açıklama ayrı Streamlit
            # öğeleriyken aralarındaki boşluğu hiçbir kural tutturamadı — kaplar
            # akışta üst üste biniyordu. Tek etiket içinde markdown satır sonu
            # (`  \n`) hem sorunu bitiriyor hem de satırın tamamını tıklanabilir
            # yapıyor; kullanıcı açıklamaya bastığında da seçim değişiyor.
            for key, (name, why) in MODES.items():
                mark = "\u2713\u2002" if key == _active else "\u2002\u2002"
                if st.button(
                    f"{mark}**{name}**  \n\u2002\u2002{why}",
                    key=f"mode_{key}", use_container_width=True,
                ):
                    st.session_state.answer_mode = key
                    st.rerun()

    if _odak_belge:
        _ad = _odak_belge["title"] or _odak_belge["filename"]
        _yer_tutucu = tc["placeholder_focus"].format(title=_ad if len(_ad) <= 40 else _ad[:39] + "…")
    else:
        _yer_tutucu = tc["placeholder"]
    prompt = st.chat_input(_yer_tutucu)

if prompt:
    # İlk soruda "Ne sorabilirsiniz" kutusu yerini sohbete bırakıyor.
    bos_kutu.empty()
    user_message = {"role": "user", "content": prompt}
    st.session_state.messages.append(user_message)
    conversations.append(st.session_state.conversation_id, user_message)
    with st.chat_message("user", avatar=_avatar("user")):
        st.markdown(prompt)
    # Soru ekrana düştüğü anda aşağı in: cevap üretimi 10-80 saniye sürebiliyor
    # ve kullanıcı o süre boyunca kendi sorusunu göremiyordu.
    scroll_to_bottom()

    with st.chat_message("assistant", avatar=_avatar("assistant")):
        answer, result = run_query(
            prompt, history=_history_for(len(st.session_state.messages) - 1)
        )

    assistant_message = {"role": "assistant", "content": answer, "result": result}
    st.session_state.messages.append(assistant_message)
    conversations.append(st.session_state.conversation_id, assistant_message)

    # Başlık YALNIZCA ilk alışverişten sonra üretiliyor. Her turda yenilemek
    # hem gereksiz bir çağrı hem de kenar çubuğunda başlıkların sürekli
    # değişmesi demek — kullanıcı listede aradığını bulamaz hâle gelir.
    if len(st.session_state.messages) == 2:
        conversations.generate_title(
            st.session_state.conversation_id, prompt, answer
        )

    st.rerun()
