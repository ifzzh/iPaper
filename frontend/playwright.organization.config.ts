import base from './playwright.keywords.config';
import {defineConfig} from '@playwright/test';
export default defineConfig({...base,testMatch:'organization.spec.ts',
 webServer:{...base.webServer as object,
 command:'cd .. && exec env PYTHON_DOTENV_DISABLED=1 IPAPER_BROWSER_KEYWORDS=1 IPAPER_BROWSER_ORGANIZE=1 .venv/bin/python -m tests.unified_server'}});
