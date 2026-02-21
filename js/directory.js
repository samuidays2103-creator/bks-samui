/* ============================================
   BKS — Directory JavaScript
   ============================================ */

let currentBusinesses = [];
let currentFilter = 'all';

const CATEGORIES = {
  realestate: { en: 'Real Estate & Rentals', ru: 'Недвижимость и аренда' },
  food: { en: 'Restaurants & Cafes', ru: 'Рестораны и кафе' },
  beauty: { en: 'Beauty & Wellness', ru: 'Красота и здоровье' },
  fitness: { en: 'Sports & Fitness', ru: 'Спорт и фитнес' },
  hotels: { en: 'Hotels & Resorts', ru: 'Отели и курорты' },
  photo: { en: 'Photo & Video', ru: 'Фото и видео' },
  education: { en: 'Education', ru: 'Образование' },
  legal: { en: 'Legal & Finance', ru: 'Юридические и финансы' },
  transport: { en: 'Transport & Logistics', ru: 'Транспорт и логистика' },
  events: { en: 'Events & Entertainment', ru: 'Мероприятия и развлечения' },
  medical: { en: 'Medical & Clinics', ru: 'Медицина и клиники' },
  services: { en: 'Other Services', ru: 'Другие услуги' }
};

const IG_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg>';

document.addEventListener('DOMContentLoaded', () => {
  loadDirectory();
});

async function loadDirectory() {
  try {
    const response = await fetch('data/businesses.json?v=' + Date.now());
    const data = await response.json();
    currentBusinesses = data.businesses;
    renderBusinessCards(currentBusinesses, 'all');
    initCategoryFilter();
  } catch (err) {
    console.error('Failed to load directory:', err);
  }
}

function renderBusinessCards(businesses, filter) {
  const grid = document.getElementById('directory-grid');
  const emptyState = document.getElementById('empty-state');
  if (!grid) return;

  const lang = getCurrentLang();
  const filtered = filter === 'all'
    ? businesses
    : businesses.filter(b => b.category === filter);

  if (filtered.length === 0) {
    grid.innerHTML = '';
    if (emptyState) emptyState.style.display = 'block';
    return;
  }

  if (emptyState) emptyState.style.display = 'none';

  grid.innerHTML = filtered.map(b => {
    const name = lang === 'ru' ? (b.nameRu || b.name) : b.name;
    const desc = lang === 'ru' ? (b.descriptionRu || b.description) : b.description;
    const cat = CATEGORIES[b.category] || { en: b.category, ru: b.category };
    const catLabel = lang === 'ru' ? cat.ru : cat.en;
    const initial = name.charAt(0).toUpperCase();
    const memberBadge = b.isMember
      ? `<span class="biz-card__member-badge">${lang === 'ru' ? 'Член БКС' : 'BKS Member'}</span>`
      : '';
    const igLink = b.instagram
      ? `<a href="${b.instagram}" target="_blank" rel="noopener" class="biz-card__link">${IG_ICON} @${b.username}</a>`
      : `<span class="biz-card__link" style="color: var(--color-text-light);">—</span>`;
    const followersText = b.followers > 0
      ? `<span class="biz-card__followers">${formatFollowers(b.followers)}</span>`
      : '';

    return `
      <div class="biz-card" data-category="${b.category}">
        ${memberBadge}
        <div class="biz-card__header">
          ${b.avatar
            ? `<img class="biz-card__avatar" src="${b.avatar}" alt="${name}" loading="lazy">`
            : `<div class="biz-card__avatar biz-card__avatar--placeholder">${initial}</div>`
          }
          <div>
            <h3 class="biz-card__name">${name}</h3>
            <span class="biz-card__category">${catLabel}</span>
          </div>
        </div>
        <p class="biz-card__desc">${desc}</p>
        <div class="biz-card__footer">
          ${igLink}
          ${followersText}
        </div>
      </div>
    `;
  }).join('');
}

function initCategoryFilter() {
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filter-btn').forEach(b =>
        b.classList.remove('filter-btn--active'));
      btn.classList.add('filter-btn--active');
      currentFilter = btn.dataset.category;
      renderBusinessCards(currentBusinesses, currentFilter);
    });
  });
}

function formatFollowers(num) {
  if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
  if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
  return num.toString();
}

function getCurrentLang() {
  return localStorage.getItem('bks-lang') || 'ru';
}

// Re-render on language change (called from main.js)
window.onLanguageChange = function(lang) {
  renderBusinessCards(currentBusinesses, currentFilter);
};
