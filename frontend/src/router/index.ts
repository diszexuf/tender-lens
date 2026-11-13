import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/calculations/new' },
    {
      path: '/calculations/new',
      name: 'calculation-new',
      component: () => import('@/pages/PlaceholderPage.vue'),
      meta: { title: 'Новый расчёт', stage: 1 },
    },
    {
      path: '/calculations',
      name: 'calculations',
      component: () => import('@/pages/PlaceholderPage.vue'),
      meta: { title: 'История расчётов', stage: 1 },
    },
    {
      path: '/sources',
      name: 'sources',
      component: () => import('@/pages/PlaceholderPage.vue'),
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
