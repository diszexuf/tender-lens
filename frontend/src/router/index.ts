import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/calculations/new' },
    {
      path: '/calculations/new',
      name: 'calculation-new',
      component: () => import('@/pages/NewCalculationPage.vue'),
      meta: { title: 'Новый расчёт' },
    },
    {
      path: '/calculations',
      name: 'calculations',
      component: () => import('@/pages/HistoryPage.vue'),
      meta: { title: 'История расчётов' },
    },
    {
      path: '/calculations/:id',
      name: 'calculation',
      component: () => import('@/pages/CalculationPage.vue'),
      meta: { title: 'Расчёт' },
    },
    {
      path: '/sources',
      name: 'sources',
      component: () => import('@/pages/SourcesPage.vue'),
      meta: { title: 'Источники', stage: 2 },
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/pages/PlaceholderPage.vue'),
      meta: { title: 'Настройки', stage: 3 },
    },
    {
      path: '/status',
      name: 'status',
      component: () => import('@/pages/StatusPage.vue'),
      meta: { title: 'Состояние сервисов' },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.afterEach((to) => {
  const title = to.meta.title as string | undefined
  document.title = title ? `${title} — Tender Lens` : 'Tender Lens'
})
