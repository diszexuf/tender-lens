import '@mdi/font/css/materialdesignicons.css'
import 'vuetify/styles'

import { createVuetify } from 'vuetify'
import { en, ru } from 'vuetify/locale'

export const vuetify = createVuetify({
  locale: {
    locale: 'ru',
    fallback: 'en',
    messages: { ru, en },
  },
  theme: {
    defaultTheme: 'light',
    themes: {
      light: {
        colors: {
          primary: '#0f766e',
          secondary: '#475569',
          background: '#f8fafc',
        },
      },
    },
  },
  defaults: {
    VCard: { rounded: 'lg' },
    VBtn: { rounded: 'lg' },
    VAlert: { variant: 'tonal' },
  },
})
