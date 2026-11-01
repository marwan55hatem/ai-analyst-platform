import { expect, test } from "@playwright/test";

import { demoUser, register, setLocale, type Locale } from "./helpers";

const LONG_NAME: Record<Locale, string> = {
  en: "The Northern Regional Wholesale and Retail Trading Company for Office Furniture, Stationery and Paper Goods Holding Group International Limited",
  ar: "الشركة الإقليمية الشمالية للتجارة بالجملة والتجزئة في الأثاث المكتبي والقرطاسية والمنتجات الورقية القابضة الدولية المحدودة للاستثمار",
};

/** A name that would need three or more lines in the header on a phone. */
for (const locale of ["en", "ar"] as const) {
  test(`a very long company name fits the header at 375 px (${locale})`, async ({ page, context, baseURL }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await setLocale(context, locale, baseURL!);
    await register(page, locale, { ...demoUser(locale), company: LONG_NAME[locale] });
    await page.goto("/settings");

    const header = page.locator("header");
    await expect(header.getByText(LONG_NAME[locale])).toBeVisible();
    const box = await header.boundingBox();
    expect(box?.height).toBeLessThanOrEqual(57);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);

    const name = await header.locator("p").first().boundingBox();
    expect(name!.y + name!.height).toBeLessThanOrEqual(box!.y + box!.height);
    const controls = await header.locator("div.ms-auto").boundingBox();
    const overlap =
      locale === "en" ? name!.x + name!.width - controls!.x : controls!.x + controls!.width - name!.x;
    expect(overlap).toBeLessThanOrEqual(0);
    for (const button of await header.locator("div.ms-auto button").all()) await expect(button).toBeVisible();
  });
}
