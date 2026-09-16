"""Test ve ölçüm için sentetik belgeler — metnin ASLI burada.

Şirket adları, kişiler ve tutarlar tamamen uydurmadır; hiçbir gerçek kurumu
temsil etmez. Belgeler bilerek birbiriyle karşılaştırılabilir kuruldu: iki
tedarik sözleşmesi aynı maddelerde farklı koşullar taşıyor, yönetmelik ise
ikisine de üst sınır koyuyor. Çapraz belge sentezi sorusu ("hangi sözleşmenin
cezai şartı yönetmeliğe aykırı?") ancak üç belge birlikte okunursa
cevaplanabiliyor.

Türkçe karakterler (ç ğ ı İ ö ş ü) bilerek yoğun: OCR'ın en çok yanıldığı yer.
"""

SOZLESME_A = {
    "dosya": "tedarik_sozlesmesi_kuzey.pdf",
    "baslik": "KUZEY LOJİSTİK A.Ş. TEDARİK SÖZLEŞMESİ",
    "sayfalar": [
        """KUZEY LOJİSTİK A.Ş. TEDARİK SÖZLEŞMESİ

Sözleşme No: TS-2026-0147
Düzenlenme Tarihi: 12 Mart 2026

MADDE 1 - TARAFLAR
Bu sözleşme, bir tarafta merkezi İstanbul Ümraniye'de bulunan Örnek Teknoloji Hizmetleri A.Ş. (bundan sonra "Alıcı" olarak anılacaktır) ile diğer tarafta merkezi İzmir Çiğli'de bulunan Kuzey Lojistik A.Ş. (bundan sonra "Tedarikçi" olarak anılacaktır) arasında aşağıdaki şartlarla imzalanmıştır.

MADDE 2 - SÖZLEŞMENİN KONUSU
Sözleşmenin konusu, Alıcı'nın veri merkezleri için ihtiyaç duyduğu sunucu kabinleri, güç dağıtım üniteleri ve soğutma ekipmanlarının Tedarikçi tarafından temin edilmesi, teslim edilmesi ve kurulumunun yapılmasıdır.

MADDE 3 - SÖZLEŞME BEDELİ
Sözleşmenin toplam bedeli KDV hariç 4.850.000 TL (dört milyon sekiz yüz elli bin Türk Lirası) olarak belirlenmiştir. Bedel üç eşit taksitte ödenecektir. İlk taksit teslimattan sonra, ikinci taksit kurulumun tamamlanmasından sonra, üçüncü taksit ise kabul tutanağının imzalanmasından sonra ödenir.""",
        """MADDE 4 - TESLİM SÜRESİ
Tedarikçi, sözleşme konusu ekipmanların tamamını sözleşmenin imzalanmasından itibaren 30 (otuz) takvim günü içinde Alıcı'nın Gebze'deki deposuna teslim etmekle yükümlüdür. Kurulum işlemleri teslimattan sonraki 15 (on beş) gün içinde tamamlanacaktır.

MADDE 5 - CEZAİ ŞART
Tedarikçi'nin teslim süresine uymaması halinde, geciken her gün için sözleşme bedelinin binde 3'ü (‰3) oranında gecikme cezası uygulanır. Toplam gecikme cezası sözleşme bedelinin yüzde 10'unu (%10) aşamaz. Gecikmenin 20 günü geçmesi halinde Alıcı sözleşmeyi tek taraflı olarak feshetme hakkına sahiptir.

MADDE 6 - GARANTİ
Tedarikçi, teslim edilen tüm ekipmanlar için kabul tarihinden itibaren 24 (yirmi dört) ay süreyle ücretsiz garanti vermektedir. Garanti süresi içinde arızalanan parçalar en geç 72 saat içinde değiştirilecektir.""",
        """MADDE 7 - GİZLİLİK
Taraflar, sözleşmenin ifası sırasında öğrendikleri ticari sırları ve kişisel verileri üçüncü kişilerle paylaşmamayı, 6698 sayılı Kişisel Verilerin Korunması Kanunu'na uygun davranmayı kabul eder. Bu yükümlülük sözleşme sona erdikten sonra 5 (beş) yıl daha devam eder.

MADDE 8 - UYUŞMAZLIKLARIN ÇÖZÜMÜ
Bu sözleşmeden doğan uyuşmazlıklarda İstanbul Anadolu Mahkemeleri ve İcra Daireleri yetkilidir.

MADDE 9 - YÜRÜRLÜK
Dokuz maddeden oluşan bu sözleşme 12 Mart 2026 tarihinde iki nüsha halinde imzalanarak yürürlüğe girmiştir.

Alıcı: Örnek Teknoloji Hizmetleri A.Ş. - Satın Alma Müdürü Selin Aydoğdu
Tedarikçi: Kuzey Lojistik A.Ş. - Genel Müdür Oğuzhan Çelik""",
    ],
}

SOZLESME_B = {
    "dosya": "tedarik_sozlesmesi_guney.pdf",
    "baslik": "GÜNEY BİLİŞİM LTD. ŞTİ. TEDARİK SÖZLEŞMESİ",
    "sayfalar": [
        """GÜNEY BİLİŞİM LTD. ŞTİ. TEDARİK SÖZLEŞMESİ

Sözleşme No: TS-2026-0203
Düzenlenme Tarihi: 4 Nisan 2026

MADDE 1 - TARAFLAR
Bu sözleşme, Örnek Teknoloji Hizmetleri A.Ş. ("Alıcı") ile merkezi Antalya Muratpaşa'da bulunan Güney Bilişim Ltd. Şti. ("Tedarikçi") arasında imzalanmıştır.

MADDE 2 - SÖZLEŞMENİN KONUSU
Sözleşmenin konusu, Alıcı'nın ofisleri için 1.200 adet dizüstü bilgisayar, 400 adet monitör ve yazılım lisanslarının temin edilmesidir.

MADDE 3 - SÖZLEŞME BEDELİ
Sözleşmenin toplam bedeli KDV hariç 21.600.000 TL (yirmi bir milyon altı yüz bin Türk Lirası) olarak belirlenmiştir. Bedelin yüzde 40'ı sipariş onayında peşin, kalan yüzde 60'ı teslimat tamamlandıktan sonra ödenir.

MADDE 4 - TESLİM SÜRESİ
Tedarikçi, ürünlerin tamamını sipariş onayından itibaren 45 (kırk beş) takvim günü içinde teslim edecektir. Kısmi teslimat Alıcı'nın yazılı onayı olmadan yapılamaz.""",
        """MADDE 5 - CEZAİ ŞART
Teslim süresinin aşılması halinde, geciken her gün için sözleşme bedelinin binde 5'i (‰5) oranında gecikme cezası uygulanır. Toplam gecikme cezası sözleşme bedelinin yüzde 25'ini (%25) aşamaz. Gecikme 30 günü aşarsa Alıcı sözleşmeyi feshedebilir ve peşin ödenen tutarın iadesini talep edebilir.

MADDE 6 - GARANTİ
Dizüstü bilgisayarlar ve monitörler için 36 (otuz altı) ay, yazılım lisansları için 12 (on iki) ay garanti süresi uygulanır.

MADDE 7 - GİZLİLİK
Taraflar, 6698 sayılı Kişisel Verilerin Korunması Kanunu kapsamındaki yükümlülüklerine uyacaktır. Gizlilik yükümlülüğü sözleşme sona erdikten sonra 3 (üç) yıl devam eder.

MADDE 8 - UYUŞMAZLIKLARIN ÇÖZÜMÜ
Uyuşmazlıklarda Antalya Mahkemeleri ve İcra Daireleri yetkilidir.

Alıcı: Örnek Teknoloji Hizmetleri A.Ş. - Satın Alma Müdürü Selin Aydoğdu
Tedarikçi: Güney Bilişim Ltd. Şti. - Şirket Müdürü Gülşen Yıldırım""",
    ],
}

YONETMELIK = {
    "dosya": "satin_alma_yonetmeligi.pdf",
    "baslik": "ÖRNEK TEKNOLOJİ HİZMETLERİ A.Ş. SATIN ALMA YÖNETMELİĞİ",
    "sayfalar": [
        """ÖRNEK TEKNOLOJİ HİZMETLERİ A.Ş. SATIN ALMA YÖNETMELİĞİ

Yürürlük Tarihi: 2 Ocak 2026
Onaylayan: Yönetim Kurulu

BİRİNCİ BÖLÜM
Amaç, Kapsam ve Tanımlar

MADDE 1 - AMAÇ
Bu yönetmeliğin amacı, şirketin mal ve hizmet alımlarında şeffaflığı, rekabeti ve bütçe disiplinini sağlamaktır.

MADDE 2 - KAPSAM
Bu yönetmelik, şirketin tüm birimlerinin yaptığı mal ve hizmet alımlarını kapsar.

İKİNCİ BÖLÜM
Onay Yetkileri ve Sözleşme Şartları

MADDE 3 - ONAY YETKİ LİMİTLERİ
Alımlar tutarına göre aşağıdaki makamların onayına tabidir: 500.000 TL'ye kadar olan alımlar Satın Alma Müdürü; 500.000 TL ile 5.000.000 TL arasındaki alımlar Genel Müdür Yardımcısı; 5.000.000 TL'yi aşan alımlar Genel Müdür ve Yönetim Kurulu onayı gerektirir.""",
        """MADDE 4 - TEKLİF ALMA ZORUNLULUĞU
1.000.000 TL'yi aşan her alım için en az üç farklı tedarikçiden yazılı teklif alınması zorunludur.

MADDE 5 - CEZAİ ŞART STANDARTLARI
Şirketin taraf olduğu tedarik sözleşmelerinde gecikme cezası günlük oranı binde 3 ile binde 5 arasında belirlenir. Toplam gecikme cezası üst sınırı sözleşme bedelinin yüzde 20'sini (%20) geçemez. Bu sınırı aşan cezai şart içeren sözleşmeler Hukuk Müşavirliği'nin yazılı görüşü alınmadan imzalanamaz.

MADDE 6 - GARANTİ ŞARTI
Donanım alımlarında garanti süresi en az 24 ay olmalıdır.

MADDE 7 - GİZLİLİK
Tedarikçilerle yapılan sözleşmelerde gizlilik yükümlülüğü sözleşme sona erdikten sonra en az 5 (beş) yıl sürmelidir.

MADDE 8 - YÜRÜRLÜK
Bu yönetmelik Yönetim Kurulu kararıyla 2 Ocak 2026 tarihinde yürürlüğe girmiştir.""",
    ],
}

TUM_BELGELER = [SOZLESME_A, SOZLESME_B, YONETMELIK]
