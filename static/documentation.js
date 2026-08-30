(function () {
    'use strict';

    const app = document.querySelector('[data-docs-app]');
    if (!app) return;

    const sidebar = app.querySelector('.docs-sidebar');
    const menuButton = app.querySelector('[data-docs-menu]');
    const overlay = app.querySelector('[data-docs-overlay]');
    const searchInput = app.querySelector('[data-docs-search]');
    const searchStatus = app.querySelector('[data-docs-search-status]');
    const sections = Array.from(app.querySelectorAll('[data-docs-section]'));
    const navLinks = Array.from(app.querySelectorAll('[data-docs-nav] a'));
    const toTop = app.querySelector('[data-docs-to-top]');
    const lightbox = app.querySelector('[data-docs-lightbox-dialog]');
    const lightboxImage = lightbox ? lightbox.querySelector('img') : null;
    const lightboxTitle = lightbox ? lightbox.querySelector('figcaption') : null;
    const lightboxTriggers = Array.from(app.querySelectorAll('[data-docs-lightbox]'));
    const lightboxCloseControls = lightbox
        ? Array.from(lightbox.querySelectorAll('[data-docs-lightbox-close]'))
        : [];

    function closeMenu() {
        app.classList.remove('is-menu-open');
        menuButton.setAttribute('aria-expanded', 'false');
    }

    menuButton.addEventListener('click', function () {
        const open = app.classList.toggle('is-menu-open');
        menuButton.setAttribute('aria-expanded', String(open));
    });
    overlay.addEventListener('click', closeMenu);
    navLinks.forEach(function (link) {
        link.addEventListener('click', closeMenu);
    });

    function closeLightbox() {
        if (!lightbox) return;
        lightbox.hidden = true;
        document.body.classList.remove('docs-lightbox-open');
    }

    lightboxTriggers.forEach(function (trigger) {
        trigger.addEventListener('click', function () {
            if (!lightbox || !lightboxImage || !lightboxTitle) return;
            lightboxImage.src = trigger.dataset.image;
            lightboxImage.alt = trigger.dataset.title;
            lightboxTitle.textContent = trigger.dataset.title;
            lightbox.hidden = false;
            document.body.classList.add('docs-lightbox-open');
            lightbox.querySelector('.docs-lightbox-close').focus();
        });
    });
    lightboxCloseControls.forEach(function (control) {
        control.addEventListener('click', closeLightbox);
    });

    function normalize(value) {
        return String(value || '').toLocaleLowerCase('ru').replace(/ё/g, 'е').trim();
    }

    function filterSections() {
        const query = normalize(searchInput.value);
        let found = 0;
        sections.forEach(function (section) {
            const haystack = normalize(section.textContent + ' ' + (section.dataset.searchTerms || ''));
            const visible = !query || haystack.includes(query);
            section.classList.toggle('docs-search-hidden', !visible);
            if (visible && query) found += 1;
        });
        searchStatus.textContent = query
            ? (found ? 'Найдено разделов: ' + found : 'Совпадений не найдено')
            : '';
        app.classList.toggle('is-searching', Boolean(query));
    }

    searchInput.addEventListener('input', filterSections);
    document.addEventListener('keydown', function (event) {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
            event.preventDefault();
            searchInput.focus();
            if (window.innerWidth <= 980) {
                app.classList.add('is-menu-open');
                menuButton.setAttribute('aria-expanded', 'true');
            }
        }
        if (event.key === 'Escape') {
            if (lightbox && !lightbox.hidden) {
                closeLightbox();
                return;
            }
            if (document.activeElement === searchInput && searchInput.value) {
                searchInput.value = '';
                filterSections();
            } else {
                closeMenu();
            }
        }
    });

    const observer = new IntersectionObserver(function (entries) {
        const visible = entries
            .filter(function (entry) { return entry.isIntersecting; })
            .sort(function (a, b) { return b.intersectionRatio - a.intersectionRatio; });
        if (!visible.length) return;
        const currentId = visible[0].target.id;
        navLinks.forEach(function (link) {
            link.classList.toggle('active', link.getAttribute('href') === '#' + currentId);
        });
        const activeLink = navLinks.find(function (link) { return link.classList.contains('active'); });
        if (activeLink && window.innerWidth > 980) {
            const linkTop = activeLink.offsetTop;
            const sidebarTop = sidebar.scrollTop;
            if (linkTop < sidebarTop + 100 || linkTop > sidebarTop + sidebar.clientHeight - 100) {
                activeLink.scrollIntoView({ block: 'center', behavior: 'smooth' });
            }
        }
    }, { rootMargin: '-18% 0px -65% 0px', threshold: [0, 0.2, 0.5] });
    sections.forEach(function (section) { observer.observe(section); });

    app.querySelectorAll('[data-docs-tabs]').forEach(function (tabs) {
        const buttons = Array.from(tabs.querySelectorAll('[role="tab"]'));
        const panels = Array.from(tabs.querySelectorAll('[role="tabpanel"]'));
        buttons.forEach(function (button) {
            button.addEventListener('click', function () {
                buttons.forEach(function (item) {
                    item.setAttribute('aria-selected', String(item === button));
                });
                panels.forEach(function (panel) {
                    panel.hidden = panel.id !== button.getAttribute('aria-controls');
                });
            });
        });
    });

    function updateToTop() {
        toTop.classList.toggle('is-visible', window.scrollY > 700);
    }
    window.addEventListener('scroll', updateToTop, { passive: true });
    toTop.addEventListener('click', function () {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    updateToTop();
})();
