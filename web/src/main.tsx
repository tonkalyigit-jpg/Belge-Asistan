import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// KaTeX'in kendi stili ŞART: ders notu sorularında cevap LaTeX içeriyor ve
// stil olmadan formüller üst üste binmiş ham semboller olarak çıkıyor.
import 'katex/dist/katex.min.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
