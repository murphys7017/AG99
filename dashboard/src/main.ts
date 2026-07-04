import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import { router } from './router';
import vuetify from './plugins/vuetify';
import confirmPlugin from './plugins/confirmPlugin';
import { setupI18n } from './i18n/composables';
import '@/scss/style.scss';
import VueApexCharts from 'vue3-apexcharts';

import print from 'vue3-print-nb';
import { loader } from '@guolao/vue-monaco-editor'
import * as monaco from 'monaco-editor';
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker';
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker';
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker';
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker';
import axios from 'axios';
import { waitForRouterReadyInBackground } from './utils/routerReadiness.mjs';
import { UPGRADE_RECOVERY_TOKEN_KEY } from './utils/upgradeRecovery';

(self as any).MonacoEnvironment = {
  getWorker(_: string, label: string) {
    if (label === 'json') {
      return new jsonWorker();
    }
    if (label === 'css' || label === 'scss' || label === 'less') {
      return new cssWorker();
    }
    if (label === 'html' || label === 'handlebars' || label === 'razor') {
      return new htmlWorker();
    }
    if (label === 'typescript' || label === 'javascript') {
      return new tsWorker();
    }
    return new editorWorker();
  },
};

// 初始化新的i18n系统，等待完成后再挂载应用
setupI18n().then(async () => {
  console.log('🌍 新i18n系统初始化完成');
  
  const app = createApp(App);
  const pinia = createPinia();
  app.use(pinia);
  app.use(router);
  app.use(print);
  app.use(VueApexCharts);
  app.use(vuetify);
  app.use(confirmPlugin);
  await router.isReady();
  app.mount('#app');
  
  // 挂载后同步 Vuetify 主题
  import('./stores/customizer').then(({ useCustomizerStore }) => {
    const customizer = useCustomizerStore(pinia);
    vuetify.theme.global.name.value = customizer.uiTheme;
    const storedPrimary = localStorage.getItem('themePrimary');
    const storedSecondary = localStorage.getItem('themeSecondary');
    if (storedPrimary || storedSecondary) {
      const themes = vuetify.theme.themes.value;
      ['PurpleTheme', 'PurpleThemeDark'].forEach((name) => {
        const theme = themes[name];
        if (!theme?.colors) return;
        if (storedPrimary) theme.colors.primary = storedPrimary;
        if (storedSecondary) theme.colors.secondary = storedSecondary;
        if (storedPrimary && theme.colors.darkprimary) theme.colors.darkprimary = storedPrimary;
        if (storedSecondary && theme.colors.darksecondary) theme.colors.darksecondary = storedSecondary;
      });
    }
  });
}).catch(error => {
  console.error('❌ 新i18n系统初始化失败:', error);
  
  // 即使i18n初始化失败，也要挂载应用（使用回退机制）
  const app = createApp(App);
  const pinia = createPinia();
  app.use(pinia);
  app.use(router);
  app.use(print);
  app.use(VueApexCharts);
  app.use(vuetify);
  app.use(confirmPlugin);
  app.mount('#app');
  waitForRouterReadyInBackground(router);
  
  // 挂载后同步 Vuetify 主题
  import('./stores/customizer').then(({ useCustomizerStore }) => {
    const customizer = useCustomizerStore(pinia);
    vuetify.theme.global.name.value = customizer.uiTheme;
    const storedPrimary = localStorage.getItem('themePrimary');
    const storedSecondary = localStorage.getItem('themeSecondary');
    if (storedPrimary || storedSecondary) {
      const themes = vuetify.theme.themes.value;
      ['PurpleTheme', 'PurpleThemeDark'].forEach((name) => {
        const theme = themes[name];
        if (!theme?.colors) return;
        if (storedPrimary) theme.colors.primary = storedPrimary;
        if (storedSecondary) theme.colors.secondary = storedSecondary;
        if (storedPrimary && theme.colors.darkprimary) theme.colors.darkprimary = storedPrimary;
        if (storedSecondary && theme.colors.darksecondary) theme.colors.darksecondary = storedSecondary;
      });
    }
  });
});


axios.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  const headers = config.headers as Record<string, unknown>;
  const hasAuthorization =
    typeof (config.headers as any)?.has === 'function'
      ? (config.headers as any).has('Authorization')
      : Boolean(headers?.Authorization || headers?.authorization);
  if (token && !hasAuthorization) {
    config.headers['Authorization'] = `Bearer ${token}`;
  }
  const locale = localStorage.getItem('astrbot-locale');
  const hasAcceptLanguage =
    typeof (config.headers as any)?.has === 'function'
      ? (config.headers as any).has('Accept-Language')
      : Boolean(headers?.['Accept-Language'] || headers?.['accept-language']);
  if (locale && !hasAcceptLanguage) {
    config.headers['Accept-Language'] = locale;
  }
  return config;
});

function clearStoredDashboardSession() {
  localStorage.removeItem('user');
  localStorage.removeItem('token');
  localStorage.removeItem('change_pwd_hint');
  localStorage.removeItem('legacy_pwd_hint');
  localStorage.removeItem('password_upgrade_required');
  sessionStorage.removeItem(UPGRADE_RECOVERY_TOKEN_KEY);
}

function getCurrentRouteForRedirect() {
  const hash = window.location.hash || '';
  if (!hash.startsWith('#/')) {
    return '/';
  }
  return hash.slice(1) || '/';
}

function isAuthChallengePath(pathname: string) {
  return [
    '/api/auth/login',
    '/api/auth/setup',
    '/api/auth/setup-status',
    '/api/auth/setup-authenticated'
  ].includes(pathname);
}

function redirectToLoginIfNeeded() {
  const currentRoute = getCurrentRouteForRedirect();
  if (currentRoute.startsWith('/auth/login')) {
    return;
  }
  const redirect = encodeURIComponent(currentRoute);
  window.location.hash = `/auth/login?redirect=${redirect}`;
}

function maybeHandleUnauthorized(urlLike: string, status: number) {
  if (status !== 401) {
    return;
  }
  try {
    const resolvedUrl = new URL(urlLike || '/', window.location.origin);
    if (resolvedUrl.origin !== window.location.origin) {
      return;
    }
    if (!resolvedUrl.pathname.startsWith('/api/')) {
      return;
    }
    if (isAuthChallengePath(resolvedUrl.pathname)) {
      return;
    }
  } catch {
    return;
  }
  clearStoredDashboardSession();
  redirectToLoginIfNeeded();
}

axios.interceptors.response.use(
  (response) => response,
  (error) => {
    const requestUrl = error?.config?.url || '';
    const baseURL = error?.config?.baseURL;
    const resolvedUrl =
      requestUrl && baseURL && !/^([a-z][a-z\d+\-.]*:)?\/\//i.test(requestUrl)
        ? `${String(baseURL).replace(/\/+$/, '')}/${String(requestUrl).replace(/^\/+/, '')}`
        : requestUrl;
    maybeHandleUnauthorized(resolvedUrl, error?.response?.status);
    return Promise.reject(error);
  }
);

// Keep fetch() calls consistent with axios by automatically attaching the JWT.
// Some parts of the UI use fetch directly; without this, those requests will 401.
const _origFetch = window.fetch.bind(window);
window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
  const token = localStorage.getItem('token');
  const locale = localStorage.getItem('astrbot-locale');

  const headers = new Headers(init?.headers || (typeof input !== 'string' && 'headers' in input ? (input as Request).headers : undefined));
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (locale && !headers.has('Accept-Language')) {
    headers.set('Accept-Language', locale);
  }
  const requestInit = token || locale ? { ...init, headers } : init;
  return _origFetch(input, requestInit).then((response) => {
    let requestUrl = '';
    if (typeof input === 'string') {
      requestUrl = input;
    } else if (input instanceof URL) {
      requestUrl = input.toString();
    } else {
      requestUrl = input.url;
    }
    maybeHandleUnauthorized(requestUrl, response.status);
    return response;
  });
};

loader.config({ monaco })
