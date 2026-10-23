import { test, expect } from "@playwright/test";
import { isValidLocator, locatorToLatlon, scatterGeometry } from "../src/lib/geo";
import { formatUsableTime, predictUsableTime } from "../src/lib/usableTime";
import type { SkyCourseAircraft } from "../src/lib/skyCourse";

const usableAircraft: SkyCourseAircraft = {
  lat: 0, lon: 0.5, altitude_ft: 10000 / 0.3048, heading_deg: 90, velocity_kts: 360,
};
const usableA = { lat: 0, lon: 0, altitudeM: 0 };
const usableB = { lat: 0, lon: 1, altitudeM: 0 };

test("usable time ends at the first path boundary and is reciprocal", () => {
  const expectedSeconds = (Math.PI * 6371 / 360) / (360 * 1.852 / 3600);
  for (const heading of [90, 270]) {
    const plane = { ...usableAircraft, heading_deg: heading };
    const forward = predictUsableTime(plane, usableA, usableB);
    const reverse = predictUsableTime(plane, usableB, usableA);
    expect(forward.seconds).toBeCloseTo(expectedSeconds, 0);
    expect(forward).toEqual(reverse);
    expect(forward.capped).toBe(false);
    expect(formatUsableTime(forward)).toBe("5:00");
    expect(predictUsableTime({ ...plane, velocity_kts: 720 }, usableA, usableB).seconds)
      .toBeCloseTo(expectedSeconds / 2, 0);
  }
});

test("crossing aircraft usable time ends at the mutual radio horizon", () => {
  const plane = { ...usableAircraft, lon: 0, heading_deg: 0, velocity_kts: 720 };
  const a = { lat: 0, lon: -1, altitudeM: 0 };
  const b = { lat: 0, lon: 1, altitudeM: 0 };
  const radius = 6371 * 4 / 3;
  const horizonKm = radius * Math.acos(radius / (radius + 10));
  const latitudeAtExit = Math.acos(Math.cos(horizonKm / 6371) / Math.cos(Math.PI / 180));
  const expectedSeconds = latitudeAtExit * 6371 / (720 * 1.852 / 3600);
  const prediction = predictUsableTime(plane, a, b);
  expect(prediction.seconds).toBeCloseTo(expectedSeconds, 0);
  expect(prediction).toEqual(predictUsableTime(plane, b, a));
  const highA = { ...a, altitudeM: 2000 };
  const elevated = predictUsableTime(plane, highA, b);
  expect(elevated.seconds!).toBeLessThan(prediction.seconds!);
  expect(elevated).toEqual(predictUsableTime(plane, b, highA));
  const coincident = predictUsableTime(plane, a, a);
  expect(coincident.seconds).toBeGreaterThan(0);
});

test("usable time distinguishes outside, missing inputs and the prediction cap", () => {
  for (const plane of [
    { ...usableAircraft, lon: -0.1 },
    { ...usableAircraft, lon: 1.1 },
    { ...usableAircraft, lat: 10 },
    { ...usableAircraft, lon: -0.1, heading_deg: null },
  ]) {
    const result = predictUsableTime(plane, usableA, usableB);
    expect(result.seconds).toBe(0);
    expect(formatUsableTime(result)).toBe("0:00");
  }
  for (const changes of [
    { heading_deg: null }, { heading_deg: NaN }, { velocity_kts: null },
    { velocity_kts: 0 }, { velocity_kts: -1 }, { velocity_kts: Infinity },
    { lat: NaN }, { lon: 181 }, { altitude_ft: NaN },
  ]) {
    const result = predictUsableTime({ ...usableAircraft, ...changes }, usableA, usableB);
    expect(result.seconds).toBeNull();
    expect(result.reason.length).toBeGreaterThan(0);
    expect(formatUsableTime(result)).toBe("—");
  }
  expect(predictUsableTime(usableAircraft, null, usableB).seconds).toBeNull();
  expect(predictUsableTime(usableAircraft, usableA, { ...usableB, altitudeM: NaN }).seconds).toBeNull();
  const capped = predictUsableTime({ ...usableAircraft, velocity_kts: 1 }, usableA, usableB);
  expect(capped.seconds).toBe(1800);
  expect(capped.capped).toBe(true);
  expect(formatUsableTime(capped)).toBe("≥30m");
});

test("scatter scoring requires inward bearings from both endpoints", () => {
  for (const [lat, lon] of [[0, -1], [1, 0], [0, 1.5], [0, 3], [1, 1]]) {
    for (const reverse of [false, true]) {
      const geo = scatterGeometry(0, reverse ? 1 : 0, 0, reverse ? 0 : 1, lat, lon, 10000);
      expect(geo.el_a).toBeGreaterThan(0);
      expect(geo.el_b).toBeGreaterThan(0);
      expect(geo.scatter_score).toBe(0);
    }
  }
  expect(scatterGeometry(40, -100, 40, -98, 40, -99, 11000).scatter_score).toBe(0.594);
  expect(scatterGeometry(0, 0, 0, 0, 0, -1, 11000).scatter_score).toBeGreaterThan(0);
});

test("DX favorites remain selectable by keyboard and Escape cancels navigation", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.favoriteDx", JSON.stringify([
      { call: "K7NAV1", grid: "CN90AA", alt: "100" },
      { call: "K7NAV2", grid: "CN91AA", alt: "200" },
    ]));
  });
  await page.goto("/");
  const picker = page.getByRole("button", { name: "DX favorites", exact: true });
  await picker.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("listbox")).toBeVisible();
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("textbox", { name: "DX callsign", exact: true })).toHaveValue("K7NAV2");
  await expect(page.getByRole("textbox", { name: "DX altitude (m)", exact: true })).toHaveValue("200");
  await expect(picker).toBeFocused();
  await page.keyboard.press("Space");
  await expect(page.getByRole("listbox")).toBeVisible();
  await page.keyboard.press("Home");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "DX callsign", exact: true })).toHaveValue("K7NAV2");
  await expect(picker).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Home");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("textbox", { name: "DX callsign", exact: true })).toHaveValue("K7NAV1");
  await expect(picker).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "+ Other", exact: true })).toBeFocused();
});

test("flight favorites support keyboard selection and restore the Only setting", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.favoriteFlights", JSON.stringify([
      { flight: "UAL123", only: true }, { flight: "DAL456", only: false },
    ]));
  });
  await page.goto("/");
  const picker = page.getByRole("button", { name: "Flight favorites", exact: true });
  await picker.focus();
  await page.keyboard.press("ArrowUp");
  await expect(page.getByRole("listbox")).toBeVisible();
  await page.keyboard.press("Home");
  await page.keyboard.press("Space");
  await expect(page.getByRole("textbox", { name: "Priority flight number" })).toHaveValue("UAL123");
  await expect(page.getByRole("checkbox", { name: "Show only priority flight" })).toBeChecked();
  await expect(picker).toBeFocused();
});

test("Filters collapses numeric fields without hiding flight controls or changing values", async ({ page }) => {
  await page.goto("/");
  const filters = page.locator(".aircraft-filters");
  const field = filters.locator('input[aria-label="Min lat"]');
  const flight = page.getByRole("textbox", { name: "Priority flight number" });
  await expect(field).toBeHidden();
  await expect(flight).toBeVisible();
  await filters.locator("summary").click();
  await field.fill("45");
  await filters.locator("summary").click();
  await expect(field).toBeHidden();
  await expect(flight).toBeVisible();
  await expect(page.getByRole("button", { name: "Flight favorites", exact: true })).toBeVisible();
  await filters.locator("summary").click();
  await expect(field).toHaveValue("45");
});

test("angular filters allow backspacing to empty and typing replacement values", async ({ page }) => {
  await page.goto("/");
  await page.locator(".aircraft-filters summary").click();
  for (const [label, defaultValue] of [["Heading ±°", "30"], ["Path bearing ±°", "70"]]) {
    const field = page.getByRole("spinbutton", { name: label, exact: true });
    await expect(field).toHaveValue(defaultValue);
    await field.focus();
    await field.press("End");
    await field.press("Backspace");
    await field.press("Backspace");
    await expect(field).toHaveValue("");
    await field.pressSequentially("180");
    await expect(field).toHaveValue("180");
    await field.press("Tab");
    await expect(field).toHaveValue("180");
    await field.fill("");
    await field.press("Tab");
    await expect(field).toHaveValue(defaultValue);
  }
});

test("Flight Data and Log have working vertical resize grips and Log keeps 30 scrollable entries", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Rig", exact: true })).toBeVisible();
  await page.evaluate(() => {
    const emit = Reflect.get(window, "__tauriMockEmit") as (event: string, payload: string) => void;
    for (let i = 0; i < 40; i++) {
      emit("sidecar-message", JSON.stringify({
        event: "wsjtx_decode",
        data: { message: `LOGTEST${i}`, snr: -10, delta_freq_hz: 1000, mode: "Q65", time_ms: 43200000 },
      }));
    }
  });
  const log = page.getByRole("region", { name: "Log", exact: true });
  await expect(log.locator(".entry")).toHaveCount(30);
  await expect(log.locator(".entry").first()).toContainText("LOGTEST39");
  await expect(log.locator(".entry").last()).toContainText("LOGTEST10");
  expect(await log.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  await log.evaluate(element => { element.scrollTop = element.scrollHeight; });
  expect(await log.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  const areas = [page.getByRole("region", { name: "Flight Data" }), page.locator(".log-card")];
  const heights: number[] = [];
  for (const [index, area] of areas.entries()) {
    await expect(area).toHaveCSS("resize", "vertical");
    await area.scrollIntoViewIfNeeded();
    const before = (await area.boundingBox())!;
    await page.mouse.move(before.x + before.width - 4, before.y + before.height - 4);
    await page.mouse.down();
    await page.mouse.move(before.x + before.width - 4, before.y + before.height - 44, { steps: 8 });
    await page.mouse.up();
    const after = (await area.boundingBox())!;
    expect(after.height).toBeLessThan(before.height - 20);
    expect(after.width).toBe(before.width);
    heights.push(after.height);
    const key = index === 0 ? "airwsjtx.flightDataHeight" : "airwsjtx.logHeight";
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), key)).toBe(String(after.height));
  }
  await page.reload();
  for (const [index, area] of areas.entries()) {
    await expect(area).toHaveCSS("height", `${heights[index]}px`);
  }
  await page.setViewportSize({ width: 1100, height: 900 });
  for (const [index, area] of areas.entries()) {
    await expect(area).toHaveCSS("height", `${heights[index]}px`);
  }
});

test("Flight Data groups source controls and heading shares the path bearing row", async ({ page }) => {
  await page.goto("/");
  await page.locator(".aircraft-filters summary").click();
  const heading = page.getByRole("spinbutton", { name: "Heading ±°", exact: true });
  const bearing = page.getByRole("spinbutton", { name: "Path bearing ±°", exact: true });
  const headingBox = await heading.boundingBox();
  const bearingBox = await bearing.boundingBox();
  expect(headingBox!.y).toBe(bearingBox!.y);
  const titleBox = await page.getByRole("heading", { name: "Flight Data", exact: true }).boundingBox();
  const sourcesBox = await page.locator("[data-aircraft-sources]").boundingBox();
  const accountsBox = await page.locator(".aircraft-accounts summary").filter({ hasText: "Aircraft accounts" }).boundingBox();
  const statusBox = await page.locator("[data-flight-status]").boundingBox();
  expect(sourcesBox!.y).toBeGreaterThanOrEqual(titleBox!.y + titleBox!.height);
  expect(accountsBox!.y).toBeGreaterThan(sourcesBox!.y);
  expect(statusBox!.y).toBeGreaterThan(sourcesBox!.y);
  await page.setViewportSize({ width: 1100, height: 900 });
  expect((await heading.boundingBox())!.y).toBe((await bearing.boundingBox())!.y);
});

test("favorite popups share typography, align DX columns, and omit title options", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.favoriteDx", JSON.stringify([
      { call: "W7GLF/P", grid: "CN88", alt: "0" },
      { call: "K7MDL", grid: "CN87TP", alt: "1300" },
    ]));
    localStorage.setItem("airwsjtx.favoriteFlights", JSON.stringify([{ flight: "DAL652", only: true }]));
  });
  await page.goto("/");
  const options = page.getByRole("listbox").getByRole("option");
  await page.getByRole("button", { name: "DX favorites", exact: true }).click();
  await expect(options).toHaveCount(2);
  await expect(options.first()).toHaveText("W7GLF/PCN880 m");
  const dxFont = await options.first().evaluate(el => ({
    family: getComputedStyle(el).fontFamily, size: getComputedStyle(el).fontSize,
  }));
  for (let column = 0; column < 3; column++) {
    expect((await options.nth(0).locator("span").nth(column).boundingBox())!.x)
      .toBe((await options.nth(1).locator("span").nth(column).boundingBox())!.x);
  }
  await page.getByRole("button", { name: "Flight favorites", exact: true }).click();
  await expect(options).toHaveCount(1);
  await expect(options.first()).toHaveText(/DAL652\s+Only: Yes/);
  await expect(options.first()).toHaveCSS("font-family", dxFont.family);
  await expect(options.first()).toHaveCSS("font-size", dxFont.size);
});

test("favorites align with station inputs and share button typography and text colors", async ({ page }) => {
  await page.goto("/");
  const dxFavorites = page.getByRole("button", { name: "DX favorites", exact: true });
  const dxInput = page.getByRole("textbox", { name: "DX callsign", exact: true });
  const dxFavoritesLabel = page.locator("#dx-favorites-label");
  await expect(dxFavorites).toHaveText("DX favorites");
  const other = page.getByRole("button", { name: "+ Other", exact: true });
  const font = await other.evaluate(el => {
    const style = getComputedStyle(el);
    return { family: style.fontFamily, size: style.fontSize, weight: style.fontWeight, color: style.color };
  });
  await expect(dxFavorites).toHaveCSS("font-family", font.family);
  await expect(dxFavorites).toHaveCSS("font-size", font.size);
  await expect(dxFavorites).toHaveCSS("font-weight", font.weight);
  await expect(dxFavorites).toHaveCSS("width", "140px");
  await expect(dxFavorites).toHaveCSS("padding-left", "6px");
  await expect(dxFavorites).toHaveCSS("padding-right", "20px");
  const flightFavorites = page.getByRole("button", { name: "Flight favorites", exact: true });
  const arrowImage = await flightFavorites.evaluate(el => getComputedStyle(el).backgroundImage);
  expect(arrowImage).not.toBe("none");
  await expect(dxFavorites).toHaveCSS("background-image", arrowImage);
  await expect(dxFavorites).toHaveCSS("background-repeat", "no-repeat");
  await expect(dxFavorites).toHaveCSS("background-position", await flightFavorites.evaluate(el => getComputedStyle(el).backgroundPosition));
  await dxFavorites.focus();
  await expect(dxFavorites).toBeFocused();
  await expect(dxFavorites).toHaveCSS("border-top-color", "rgb(139, 148, 158)");
  await expect(dxFavorites).toHaveCSS("border-top-style", "solid");
  await expect(dxFavorites).toHaveCSS("outline-color", "rgb(139, 148, 158)");
  await expect(dxFavorites).toHaveCSS("outline-style", "solid");
  await flightFavorites.click();
  await dxFavorites.click();
  await expect(dxFavorites).toHaveCSS("outline-style", "none");
  await dxFavorites.click();
  await other.focus();
  await expect(dxFavorites).toHaveCSS("border-top-color", "rgb(139, 148, 158)");
  for (const button of await page.locator(".display-toolbar .portable-mode").all()) {
    await expect(button).toHaveCSS("font-size", font.size);
    await expect(button).toHaveCSS("font-family", font.family);
    await expect(button).toHaveCSS("font-weight", font.weight);
    await expect(button).toHaveCSS("color", font.color);
  }
  for (const control of [
    dxFavorites,
    page.getByRole("button", { name: "Flight favorites", exact: true }),
    page.getByRole("button", { name: "Clear flight", exact: true }),
  ]) {
    await expect(control).toHaveCSS("color", font.color);
  }
  for (const width of [1440, 1100]) {
    await page.setViewportSize({ width, height: 900 });
    expect((await dxFavorites.boundingBox())!.x).toBe((await dxInput.boundingBox())!.x);
    expect((await dxFavoritesLabel.boundingBox())!.x).toBe((await dxInput.boundingBox())!.x - 20);
  }
});

test("data-entry borders are lighter without changing card borders", async ({ page }) => {
  await page.goto("/");
  await page.locator(".aircraft-filters summary").click();
  for (const field of [
    page.getByRole("textbox", { name: "MY callsign", exact: true }),
    page.getByRole("textbox", { name: "Priority flight number" }),
    page.getByRole("spinbutton", { name: "Min lat", exact: true }),
    page.getByRole("button", { name: "DX favorites", exact: true }),
    page.getByRole("button", { name: "Flight favorites", exact: true }),
  ]) {
    await expect(field).toHaveCSS("border-top-color", "rgb(139, 148, 158)");
  }
  await expect(page.locator(".card").first()).toHaveCSS("border-top-color", "rgb(48, 54, 61)");
});

test("flight label is above its input and favorites wrap within a narrow panel", async ({ page }) => {
  await page.goto("/");
  const controls = page.locator("[data-flight-controls]");
  await controls.evaluate(element => { element.style.width = "300px"; });
  const field = page.getByRole("textbox", { name: "Priority flight number" });
  const labelBox = await page.locator('label[for="priority-flight"]').boundingBox();
  const fieldBox = await field.boundingBox();
  expect(labelBox!.y + labelBox!.height).toBeLessThanOrEqual(fieldBox!.y);
  const picker = page.getByRole("button", { name: "Flight favorites", exact: true });
  const pickerBox = await picker.boundingBox();
  const controlsBox = await controls.boundingBox();
  expect(pickerBox!.y).toBeGreaterThan(fieldBox!.y);
  expect(pickerBox!.y - fieldBox!.y - fieldBox!.height).toBeGreaterThanOrEqual(10);
  const onlyLabel = page.getByRole("checkbox", { name: "Show only priority flight" }).locator("..");
  await expect(onlyLabel).toHaveCSS("padding-left", "5px");
  await expect(onlyLabel).toHaveCSS("column-gap", "5px");
  const titleBox = await page.getByRole("heading", { name: "Flight Data", exact: true }).boundingBox();
  expect(titleBox!.y - controlsBox!.y - controlsBox!.height).toBeGreaterThanOrEqual(12);
  expect(pickerBox!.x + pickerBox!.width).toBeLessThanOrEqual(controlsBox!.x + controlsBox!.width);
  await picker.click();
  const menuBox = await page.getByRole("listbox").boundingBox();
  expect(menuBox!.x + menuBox!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.setViewportSize({ width: 1100, height: 900 });
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await picker.click();
  const resizedMenu = await page.getByRole("listbox").boundingBox();
  expect(resizedMenu!.x + resizedMenu!.width).toBeLessThanOrEqual(1100);
});

test("Flight Data shows all scored aircraft while candidates keep their selection and highlight the watched flight", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.favoriteFlights", JSON.stringify([
      { flight: "SAVED1", only: false },
    ]));
  });
  await page.goto("/");
  await page.getByRole("textbox", { name: "Priority flight number" }).fill("ACTIVE1");
  const planes = Array.from({ length: 30 }, (_, i) => ({
    icao24: `ac${i}`, callsign: i === 0 ? "ACTIVE1" : i === 1 ? "SAVED1" : `OTHER${i}`,
    lat: 47.5, lon: -122.4, altitude_ft: 30000, heading_deg: 270, velocity_kts: 450,
    az_a: 270, el_a: 5, az_b: 230, el_b: 6, off_path_deg: 168, range_a_km: 151,
    scatter_score: i / 100,
  }));
  await page.evaluate(planes => {
    const emit = Reflect.get(window, "__tauriMockEmit") as (event: string, payload: string) => void;
    emit("sidecar-message", JSON.stringify({
      event: "airscout_update",
      data: { aircraft: planes.slice(0, 2), flight_data: planes, total: planes.length,
        station_a: null, station_b: null },
    }));
  }, planes);
  const candidates = page.locator(".plane-candidate");
  await expect(candidates).toHaveCount(2);
  await expect(candidates.first()).toContainText("ACTIVE1");
  const activeCall = candidates.first().locator('span[title="ACTIVE1"]');
  await expect(activeCall).toHaveCSS("text-decoration-line", "underline");
  expect(await activeCall.evaluate(element => {
    const style = getComputedStyle(element);
    return style.textDecorationColor === style.color;
  })).toBe(true);
  await expect(candidates.first()).toHaveCSS("outline-style", "none");
  await expect(candidates.last()).not.toHaveAttribute("data-priority-flight");
  const region = page.getByRole("region", { name: "Flight Data" });
  const rows = region.locator("tbody tr");
  await expect(rows).toHaveCount(30);
  await expect(rows.first()).toContainText("OTHER29");
  await expect(rows.last()).toContainText("ACTIVE1");
  await expect(region.locator("[data-flight-favorite]")).toHaveCount(1);
  await expect(region.locator("[data-flight-favorite]")).toContainText("SAVED1");
  await expect(region.locator("[data-flight-favorite]")).toHaveCSS("background-color", "rgb(48, 54, 61)");
  expect(await region.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  await region.evaluate(element => { element.scrollTop = element.scrollHeight; });
  await expect(rows.last()).toBeInViewport();
});

test("Flight Data distinguishes reported heading from station azimuth and missing heading", async ({ page }) => {
  await page.setViewportSize({ width: 840, height: 800 });
  await page.goto("/");
  const planes = [0, 270, 359.6, null].map((heading, i) => ({
    icao24: `heading${i}`, callsign: `HDG${i}`,
    lat: 47.5, lon: -122.4, altitude_ft: 30000, heading_deg: heading, velocity_kts: 450,
    az_a: 345, el_a: 1, az_b: 173, el_b: 2, off_path_deg: 7, range_a_km: 289,
    scatter_score: 0.38 - i / 100,
  }));
  await page.evaluate(planes => {
    const emit = Reflect.get(window, "__tauriMockEmit") as (event: string, payload: string) => void;
    emit("sidecar-message", JSON.stringify({
      event: "airscout_update",
      data: { aircraft: [], flight_data: planes, total: planes.length,
        station_a: "CN84XS67", station_b: "CN89JI" },
    }));
  }, planes);
  const region = page.getByRole("region", { name: "Flight Data" });
  const header = region.getByRole("columnheader", { name: "HDG°", exact: true });
  await expect(header).toBeVisible();
  await expect(header).toHaveAttribute("title", /ground-track.*true north/);
  const rows = region.locator("tbody tr");
  await expect(rows).toHaveCount(4);
  for (const [i, expected] of ["0°", "270°", "0°", "—"].entries()) {
    await expect(rows.nth(i).locator("td").nth(3)).toHaveText(expected);
    await expect(rows.nth(i).locator("td").nth(4)).toHaveText("345°");
  }
  await expect(rows.last().locator("td").nth(3)).toHaveAttribute("title", "Aircraft heading not reported");
  expect(await region.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await expect(page.locator(".plane-candidate")).toHaveCount(0);
});

test("Flight Data usable time is display-only and uses applied station geometry", async ({ page }) => {
  await page.setViewportSize({ width: 840, height: 800 });
  await page.goto("/");
  const a = { ...usableA, lat: 0.5, lon: 1 };
  const b = { ...usableB, lat: 0.5, lon: 3 };
  const planes = [360, 1, null, 360].map((speed, i) => ({
    icao24: `usable${i}`, callsign: `USE${i}`,
    lat: 0.5, lon: i === 3 ? 0 : 2, altitude_ft: 10000 / 0.3048,
    heading_deg: 90, velocity_kts: speed, az_a: 90, el_a: 4, az_b: 270,
    el_b: 4, off_path_deg: 0, range_a_km: 111, scatter_score: 0.38 - i / 100,
  }));
  const send = async (reverse = false, altitude = 0) => {
    await page.evaluate(({ planes, reverse, altitude }) => {
      const emit = Reflect.get(window, "__tauriMockEmit") as (event: string, payload: string) => void;
      emit("sidecar-message", JSON.stringify({
        event: "airscout_update",
        data: { aircraft: [planes[0]], flight_data: planes, total: planes.length,
          station_a: reverse ? "JJ10" : "JJ00", station_b: reverse ? "JJ00" : "JJ10",
          station_a_alt_m: reverse ? 0 : altitude, station_b_alt_m: reverse ? altitude : 0 },
      }));
    }, { planes, reverse, altitude });
  };
  await send();
  const region = page.getByRole("region", { name: "Flight Data" });
  await expect(region.getByRole("columnheader", { name: "Use", exact: true })).toHaveAttribute("title", /ignores optional filters and score/);
  const cells = region.locator("tbody tr td:last-child");
  const expected = formatUsableTime(predictUsableTime(planes[0], a, b));
  await expect(cells).toHaveText([expected, "≥30m", "—", "0:00"]);
  await expect(cells.nth(2)).toHaveAttribute("title", /ground speed is unavailable/);
  const scores = await region.locator("tbody tr td:nth-child(3)").allTextContents();
  await expect(page.locator(".plane-candidate")).toHaveCount(1);
  await expect(page.locator(".plane-candidate")).toContainText("USE0");
  await send(true);
  await expect(cells).toHaveText([expected, "≥30m", "—", "0:00"]);
  await page.getByRole("textbox", { name: "DX altitude (m)", exact: true }).fill("9000");
  await expect(cells.first()).toHaveText(expected);
  await send(false, 9000);
  await expect(cells.first()).toHaveText(formatUsableTime(predictUsableTime(planes[0], { ...a, altitudeM: 9000 }, b)));
  expect(await region.locator("tbody tr td:nth-child(3)").allTextContents()).toEqual(scores);
  expect(await region.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
});

test("flight favorites store and restore flight number and Only mode across reloads", async ({ page }) => {
  await page.goto("/");
  const flight = page.getByRole("textbox", { name: "Priority flight number" });
  const only = page.getByRole("checkbox", { name: "Show only priority flight" });
  const store = page.getByRole("button", { name: "Store Flight#", exact: true });
  const favorites = page.getByRole("button", { name: "Flight favorites", exact: true });
  const options = page.getByRole("listbox").getByRole("option");
  const chooseFavorite = async (index: number) => {
    await favorites.click();
    await options.nth(index).click();
  };
  await expect(store).toBeDisabled();
  await expect(page.getByRole("button", { name: "Clear flight", exact: true }))
    .toHaveCSS("border-top-color", "rgb(139, 148, 158)");
  await expect(store).toHaveCSS("border-top-color", "rgb(139, 148, 158)");
  await flight.fill("ual123");
  await expect(store).toHaveCSS("border-top-color", "rgb(139, 148, 158)");
  await expect(store).toHaveCSS("color", "rgb(201, 209, 217)");
  await expect(store).toHaveCSS("background-color", "rgb(22, 27, 34)");
  await only.check();
  await favorites.click();
  await expect(options).toHaveCount(0);
  await favorites.click();
  await store.click();
  await favorites.click();
  await expect(options.nth(0)).toHaveText(/UAL123\s+Only: Yes/);
  await expect(page.getByRole("listbox")).toHaveCSS("background-color", "rgb(0, 0, 0)");
  await expect(options.nth(0)).toHaveCSS("background-color", "rgb(0, 0, 0)");
  await favorites.click();
  await only.uncheck();
  await store.click();
  await favorites.click();
  await expect(options).toHaveCount(2);
  await favorites.click();
  await chooseFavorite(1);
  await expect(flight).toHaveValue("UAL123");
  await expect(only).toBeChecked();
  await store.click();
  await favorites.click();
  await expect(options).toHaveCount(2);
  await expect(options.nth(0)).toHaveText(/UAL123\s+Only: Yes/);
  await favorites.click();

  await page.reload();
  await expect(flight).toHaveValue("");
  await expect(only).not.toBeChecked();
  await favorites.click();
  await expect(options).toHaveCount(2);
  await favorites.click();
  await flight.fill("DAL456");
  await chooseFavorite(1);
  await expect(flight).toHaveValue("UAL123");
  await expect(only).not.toBeChecked();
  await chooseFavorite(0);
  await expect(only).toBeChecked();
  await expect.poll(() => page.evaluate(() => {
    const requests = Reflect.get(window, "__mockRpcRequests") as {
      method: string; params: { filters?: Record<string, unknown> };
    }[];
    return requests.filter(r => r.method === "airscout_apply_filters").at(-1)?.params.filters;
  })).toMatchObject({ priority_flight: "UAL123", priority_flight_exclusive: true });
  await page.getByRole("button", { name: "Clear flight", exact: true }).click();
  await expect(flight).toHaveValue("");
  await expect(only).not.toBeChecked();
  await expect.poll(() => page.evaluate(() => {
    const requests = Reflect.get(window, "__mockRpcRequests") as {
      method: string; params: { filters?: Record<string, unknown> };
    }[];
    return requests.filter(r => r.method === "airscout_apply_filters").at(-1)?.params.filters;
  })).toMatchObject({ priority_flight: "", priority_flight_exclusive: false });
  await chooseFavorite(0);
  await expect(flight).toHaveValue("UAL123");
  await expect(only).toBeChecked();
  await expect.poll(() => page.evaluate(() =>
    JSON.parse(localStorage.getItem("airwsjtx.favoriteFlights") ?? "[]")
  )).toEqual([{ flight: "UAL123", only: true }, { flight: "UAL123", only: false }]);
  await flight.fill("ABCDEFGH");
  expect(await flight.evaluate((element: HTMLInputElement) => {
    const style = getComputedStyle(element);
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d")!;
    context.font = `${style.fontSize} ${style.fontFamily}`;
    return element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
      >= context.measureText(element.value).width;
  })).toBe(true);
  await page.locator(".aircraft-filters summary").click();
  const flightBox = await flight.boundingBox();
  const storeBox = await store.boundingBox();
  const pickerBox = await favorites.boundingBox();
  expect(flightBox).not.toBeNull();
  expect(storeBox).not.toBeNull();
  expect(pickerBox).not.toBeNull();
  expect(flightBox!.width).toBeGreaterThanOrEqual(80);
  expect(pickerBox!.width).toBe(120);
  await favorites.click();
  await expect(page.getByRole("listbox")).toHaveCSS("width", "145px");
  await favorites.click();
  expect(storeBox!.x).toBeGreaterThan(flightBox!.x + flightBox!.width);
  const clearBox = await page.getByRole("button", { name: "Clear flight" }).boundingBox();
  for (const box of [flightBox!, storeBox!, clearBox!]) {
    expect(box.height).toBe(22);
    expect(box.y).toBe(pickerBox!.y);
  }
  expect(pickerBox!.height).toBe(22);
  expect(clearBox!.x - storeBox!.x - storeBox!.width).toBeCloseTo(8, 1);
  expect(pickerBox!.x - clearBox!.x - clearBox!.width).toBeCloseTo(8, 1);
  const numericFields = page.locator('input[aria-label="Min lat"], input[aria-label="Min alt ft"], input[aria-label="Path bearing ±°"]');
  for (const field of await numericFields.all()) {
    const box = await field.boundingBox();
    expect(box!.width).toBeLessThanOrEqual(100);
    expect(box!.y + box!.height).toBeLessThan(flightBox!.y);
  }
  await page.getByRole("spinbutton", { name: "Min alt ft", exact: true }).fill("99999");
  await expect(page.getByRole("spinbutton", { name: "Min alt ft", exact: true })).toHaveValue("99999");
});

test("flight favorites retain the 20 newest entries like DX favorites", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.favoriteFlights", JSON.stringify(
      Array.from({ length: 20 }, (_, i) => ({ flight: `UAL${i}`, only: false })),
    ));
  });
  await page.goto("/");
  await page.getByRole("textbox", { name: "Priority flight number" }).fill("DAL456");
  await page.getByRole("button", { name: "Store Flight#", exact: true }).click();
  await page.getByRole("button", { name: "Flight favorites", exact: true }).click();
  const options = page.getByRole("listbox").getByRole("option");
  await expect(options).toHaveCount(20);
  await expect(options.nth(0)).toHaveText(/DAL456\s+Only: No/);
  await expect(options.last()).toHaveText(/UAL18\s+Only: No/);
});

test("distance calculations use valid 4, 6, and 8 character grid centers", () => {
  expect(locatorToLatlon("CN87")).toEqual([47.5, -123]);
  const six = locatorToLatlon(" cn87vp ");
  expect(six[0]).toBeCloseTo(47 + 15.5 / 24, 10);
  expect(six[1]).toBeCloseTo(-124 + 21.5 * 2 / 24, 10);
  const eight = locatorToLatlon("CN87VP00");
  expect(eight[0]).toBeCloseTo(47 + 15 / 24 + 0.5 / 240, 10);
  expect(eight[1]).toBeCloseTo(-124 + 21 * 2 / 24 + 0.5 * 2 / 240, 10);
  for (const grid of [null, "", "ZZ99", "CN87V", "CN87VPXX"]) {
    expect(isValidLocator(grid)).toBe(false);
  }
  expect(() => locatorToLatlon("ZZ99")).toThrow("Invalid locator");
});

test("station distance follows active MY and DX grids and handles unknown grids", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.favoriteDx", JSON.stringify([{ call: "VE7NX", grid: "CN89" }]));
  });
  await page.goto("/");
  const distance = page.locator(".station-distance");
  await expect(distance).toHaveText("← -- km →");
  await page.getByRole("textbox", { name: "MY callsign", exact: true }).fill("K7MDL");
  await page.getByRole("textbox", { name: "MY grid", exact: true }).fill("CN87");
  await page.getByRole("button", { name: "Apply MY station" }).click();
  await page.getByRole("button", { name: "DX favorites", exact: true }).click();
  await page.getByRole("listbox").getByRole("option").first().click();
  await expect(distance).toHaveText("← 222.4 km →");
  await page.getByRole("textbox", { name: "MY grid", exact: true }).fill("CN89");
  await page.getByRole("button", { name: "Apply MY station" }).click();
  await expect(distance).toHaveText("← 0.0 km →");
  await page.getByRole("button", { name: "MY station source", exact: true }).click();
  await expect(distance).toHaveText("← -- km →");
});

for (const { source, interval } of [
  { source: "opensky", interval: 30 },
  { source: "opensky", interval: 10 },
  { source: "adsbexchange", interval: 60 },
  { source: "dump1090", interval: 5 },
]) {
test(`locked tile becomes stale only beyond ${source} ${interval}s poll plus 5s`, async ({ page }) => {
  await page.clock.install({ time: new Date("2026-01-01T00:00:00Z") });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Rig", exact: true })).toBeVisible();
  await page.clock.pauseAt(new Date("2026-01-01T00:01:00Z"));
  await expect(page.getByText("opensky", { exact: true })).toBeVisible();
  await page.evaluate(({ source, interval }) => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "airscout_source_status", data: {
        active_source: source, priority: [source],
        sources: { [source]: { state: "connected", message: null } },
        poll_intervals_s: { [source]: interval },
      },
    }));
  }, { source, interval });
  const sendTracking = async (event: string, age: number, id = "abc123") => {
    await page.evaluate(({ event, age, id }) => {
      const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
      emit("sidecar-message", JSON.stringify({
        event, data: {
          icao24: id, callsign: event === "plane_locked" ? "INITIAL" : "UPDATED",
          altitude_ft: event === "plane_locked" ? 30000 : 35000,
          range_a_km: event === "plane_locked" ? 100 : 125.6,
          az_a: 123, el_a: 5, az_b: event === "plane_locked" ? 180 : 234,
          el_b: event === "plane_locked" ? 3 : 6, scatter_score: 0.8,
          off_path_deg: event === "plane_locked" ? 20 : 12, state: "tracking",
          position_updated_epoch_s: Math.floor(Date.now() / 1000) - age,
        },
      }));
    }, { event, age, id });
  };
  await sendTracking("plane_locked", 0);
  await expect(page.locator(".locked-plane")).toContainText("km 100.0");
  const age = page.locator(".locked-update-age");
  await page.clock.runFor((interval + 5) * 1000 + 999);
  await expect(age).toContainText(`${interval + 5}s ago`);
  await expect(age).not.toContainText("stale");
  await page.clock.runFor(1000);
  await expect(age).toContainText(`${interval + 6}s ago — stale`);
  await sendTracking("plane_tracking", 0, "another-plane");
  await expect(page.locator(".locked-plane")).toContainText("km 100.0");
  await expect(age).toContainText(`${interval + 6}s ago — stale`);
  await page.evaluate(({ source, interval }) => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "airscout_source_status", data: {
        active_source: source, priority: [source],
        sources: { [source]: { state: "connected", message: null } },
        poll_intervals_s: { [source]: interval + 10 },
      },
    }));
  }, { source, interval });
  await expect(age).not.toContainText("stale");
  await page.evaluate(() => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "airscout_source_status", data: {
        active_source: "dump1090", priority: ["dump1090"],
        sources: { dump1090: { state: "connected", message: null } },
        poll_intervals_s: { dump1090: 5 },
      },
    }));
  });
  await expect(age).toContainText("stale");
  await sendTracking("plane_tracking", 0);
  await expect(age).toContainText("0s ago");
  await expect(age).not.toContainText("stale");
  const tile = page.locator(".locked-plane");
  await expect(tile).toContainText("UPDATED");
  await expect(tile).toContainText("35,000 ft");
  await expect(tile).toContainText("km 125.6");
  await expect(tile).toContainText("234.0°");
  await expect(tile).toContainText("6.0°");
  await expect(tile).toContainText("12° off-path");
});
}

test("candidate text stays within the outline at the minimum window width", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 720 });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Rig", exact: true })).toBeVisible();
  await page.evaluate(() => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "airscout_update",
      data: {
        total: 1, station_a: "CN87", station_b: "CN89",
        aircraft: [{
          icao24: "abc123", callsign: "LONGCALLSIGN12345", altitude_ft: 45000,
          scatter_score: 1, az_a: 359, el_a: 90, az_b: 180, el_b: 10,
          off_path_deg: 180,
        }],
      },
    }));
    emit("sidecar-message", JSON.stringify({
      event: "plane_locked",
      data: {
        icao24: "abc123", callsign: "LONGCALLSIGN12345", altitude_ft: 45000,
        scatter_score: 1, az_a: 359, el_a: 90, az_b: 180, el_b: 10,
        off_path_deg: 180, state: "plane_locked", range_a_km: 125.6,
        rotator_az: 0, rotator_el: 0,
      },
    }));
  });
  const row = page.locator(".plane-candidate");
  await expect(row).toBeVisible();
  const contained = await row.evaluate(element => {
    const bounds = element.getBoundingClientRect();
    return element.scrollWidth <= element.clientWidth
      && [...element.children].every(child => {
        const rect = child.getBoundingClientRect();
        return rect.left >= bounds.left && rect.right <= bounds.right
          && rect.top >= bounds.top && rect.bottom <= bounds.bottom;
      });
  });
  expect(contained).toBe(true);
  await expect(row).toContainText("45,000 ft");
  await expect(row).toContainText("AZ/EL 359°/90°");
  await expect(row.locator("[title='LONGCALLSIGN12345']")).toBeVisible();
  const locked = page.locator(".locked-plane");
  await expect(locked.locator("[title='LONGCALLSIGN12345']")).toHaveCSS("text-align", "center");
  const iconGap = await locked.locator("[title='LONGCALLSIGN12345']").evaluate(element => {
    const icon = element.closest(".locked-plane")!.querySelector("svg")!.getBoundingClientRect();
    return element.getBoundingClientRect().left - icon.right;
  });
  expect(iconGap).toBeGreaterThanOrEqual(12);
  await expect(locked).toContainText("Aircraft altitude: 45,000 ft");
  await expect(locked).toContainText("km 125.6");
  const altitude = locked.getByText(/Aircraft altitude:/);
  await expect(altitude).toBeVisible();
  const fits = await altitude.evaluate(element => {
    const tile = element.closest(".locked-plane")!.getBoundingClientRect();
    const text = element.getBoundingClientRect();
    return text.left >= tile.left && text.right <= tile.right;
  });
  expect(fits).toBe(true);
  const stationLayout = await locked.locator(".locked-stations").evaluate(element => {
    const [my, dx] = [...element.children].map(child => child.getBoundingClientRect());
    return { sameRow: Math.abs(my.top - dx.top) < 1, sideBySide: my.right <= dx.left };
  });
  expect(stationLayout).toEqual({ sameRow: true, sideBySide: true });
  expect(await locked.evaluate(element => element.getBoundingClientRect().height)).toBeLessThan(240);
  await page.setViewportSize({ width: 900, height: 1080 });
  const card = page.locator(".plane-lock-card");
  await expect(card).toBeVisible();
  const unusedSpace = await card.evaluate(element => {
    const content = element.firstElementChild!.getBoundingClientRect();
    return element.getBoundingClientRect().bottom - content.bottom;
  });
  expect(unusedSpace).toBeLessThanOrEqual(10);
});
