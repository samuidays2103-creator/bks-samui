/* ============================================
   BKS — Main JavaScript
   ============================================ */

document.addEventListener('DOMContentLoaded', () => {
  initHeader();
  initMobileNav();
  initLangSwitch();
  initScrollAnimations();
  initLightbox();
  initCounterAnimation();
});

/* --- Sticky Header --- */
function initHeader() {
  const header = document.querySelector('.header');
  if (!header) return;

  window.addEventListener('scroll', () => {
    header.classList.toggle('header--scrolled', window.scrollY > 10);
  }, { passive: true });
}

/* --- Mobile Nav --- */
function initMobileNav() {
  const hamburger = document.querySelector('.hamburger');
  const nav = document.querySelector('.nav');
  if (!hamburger || !nav) return;

  const header = hamburger.closest('.header');
  hamburger.addEventListener('click', () => {
    const isOpen = nav.classList.toggle('nav--open');
    hamburger.classList.toggle('hamburger--open');
    hamburger.setAttribute('aria-expanded', isOpen);
    header.classList.toggle('header--menu-open', isOpen);
    document.body.style.overflow = isOpen ? 'hidden' : '';
  });

  nav.querySelectorAll('.nav__link').forEach(link => {
    link.addEventListener('click', () => {
      nav.classList.remove('nav--open');
      hamburger.classList.remove('hamburger--open');
      hamburger.setAttribute('aria-expanded', 'false');
      header.classList.remove('header--menu-open');
      document.body.style.overflow = '';
    });
  });
}

/* --- Language Switch --- */
function initLangSwitch() {
  const savedLang = localStorage.getItem('bks-lang') || 'ru';
  setLanguage(savedLang);

  document.querySelectorAll('.lang-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const lang = btn.dataset.lang;
      setLanguage(lang);
      localStorage.setItem('bks-lang', lang);
    });
  });
}

function setLanguage(lang) {
  // Update buttons
  document.querySelectorAll('.lang-btn').forEach(btn => {
    btn.classList.toggle('lang-btn--active', btn.dataset.lang === lang);
  });

  // Update text content
  document.querySelectorAll('[data-en][data-ru]').forEach(el => {
    el.textContent = el.dataset[lang];
  });

  // Show/hide language-specific blocks (exclude lang switch buttons)
  document.querySelectorAll('[data-lang]:not(.lang-btn)').forEach(el => {
    el.style.display = el.dataset.lang === lang ? '' : 'none';
  });

  // Update html lang attribute
  document.documentElement.lang = lang === 'ru' ? 'ru' : 'en';

  // Notify directory.js if loaded
  if (typeof window.onLanguageChange === 'function') {
    window.onLanguageChange(lang);
  }
}

function getCurrentLang() {
  return localStorage.getItem('bks-lang') || 'ru';
}

/* --- Lightbox --- */
function initLightbox() {
  const overlay = document.createElement('div');
  overlay.className = 'lightbox';
  overlay.innerHTML = '<img class="lightbox__img" src="" alt="">';
  document.body.appendChild(overlay);

  const img = overlay.querySelector('.lightbox__img');

  document.addEventListener('click', (e) => {
    if (e.target.matches('.gallery img, [data-lightbox] img')) {
      img.src = e.target.src;
      img.alt = e.target.alt;
      overlay.classList.add('lightbox--open');
      document.body.style.overflow = 'hidden';
    }
  });

  overlay.addEventListener('click', () => {
    overlay.classList.remove('lightbox--open');
    document.body.style.overflow = '';
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && overlay.classList.contains('lightbox--open')) {
      overlay.classList.remove('lightbox--open');
      document.body.style.overflow = '';
    }
  });
}

/* --- Scroll Animations --- */
function initScrollAnimations() {
  const elements = document.querySelectorAll('.fade-in');
  if (!elements.length) return;

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('fade-in--visible');
        observer.unobserve(entry.target);
      }
    });
  }, {
    threshold: 0.1,
    rootMargin: '0px 0px -40px 0px'
  });

  elements.forEach(el => observer.observe(el));
}

/* --- Counter Animation --- */
function initCounterAnimation() {
  const stats = document.querySelectorAll('.stat__number');
  if (!stats.length) return;

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        animateCounter(entry.target);
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.5 });

  stats.forEach(el => observer.observe(el));
}

function animateCounter(el) {
  const text = el.textContent.trim();
  const match = text.match(/^(\d+)(\+?)$/);
  if (!match) return;

  const target = parseInt(match[1]);
  const suffix = match[2];
  const duration = 1500;
  const start = performance.now();

  function update(now) {
    const progress = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = Math.round(target * eased);
    el.textContent = current + suffix;
    if (progress < 1) requestAnimationFrame(update);
  }

  el.textContent = '0' + suffix;
  requestAnimationFrame(update);
}
