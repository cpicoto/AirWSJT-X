import { test, expect, type Page } from "@playwright/test";
import { destinationLatlon, elevationDeg, haversineKm, locatorToLatlon, scatterGeometry } from "../src/lib/geo";
import { predictSkyCourse, type SkyCourseAircraft } from "../src/lib/skyCourse";

const courseStation = { lat: 0, lon: 0, altitudeM: 100 };
const courseAircraft: SkyCourseAircraft = {
  lat: 0, lon: -0.2, altitude_ft: 33000, heading_deg: 90, velocity_kts: 450,
};

test("course projection follows ground speed, track and station-relative elevation", () => {
  const course = predictSkyCourse(courseAircraft, courseStation);
  expect(course.unavailableReason).toBeNull();
  const current = course.points.find(point => point.seconds === 0)!;
  const minute = course.points.find(point => point.seconds === 60)!;
  expect(current.az).toBeCloseTo(270, 6);
  const distance = 450 * 1.852 / 60;
  const [lat, lon] = destinationLatlon(0, -0.2, 90, distance);
  expect(haversineKm(0, -0.2, lat, lon)).toBeCloseTo(distance, 6);
  expect(minute.el).toBeCloseTo(elevationDeg(haversineKm(0, 0, lat, lon), 33000 * 0.3048, 100), 8);
  expect(minute.el).toBeGreaterThan(current.el);
  expect(Math.max(...course.points.map(point => point.el))).toBeGreaterThan(85);
  expect(course.points.find(point => point.seconds === 300)!.az).toBeCloseTo(90, 6);
  expect(course.points.find(point => point.seconds === 300)!.el).toBeLessThan(current.el);
  for (const seconds of [60, 120, 180, 240, 300]) {
    expect(course.points.some(point => point.seconds === seconds)).toBe(true);
  }
  const higherStation = predictSkyCourse(courseAircraft, { ...courseStation, altitudeM: 2000 });
  expect(higherStation.points.find(point => point.seconds === 0)!.el).toBeLessThan(current.el);
});

test("course ends at the radio horizon instead of drawing below it", () => {
  const { points } = predictSkyCourse({ ...courseAircraft, velocity_kts: 500 }, courseStation);
  expect(points[0].el).toBe(0);
  expect(points[points.length - 1].el).toBe(0);
  expect(points[0].seconds).toBeGreaterThan(-1800);
  expect(points[points.length - 1].seconds).toBeLessThan(1800);
  for (const [index, point] of points.entries()) {
    expect(point.el).toBeGreaterThanOrEqual(0);
    expect(point.el).toBeLessThanOrEqual(90);
    expect(point.az).toBeGreaterThanOrEqual(0);
    expect(point.az).toBeLessThan(360);
    if (index) expect(point.seconds).toBeGreaterThan(points[index - 1].seconds);
  }
});

test("geodesic prediction handles north, the date line and near-polar tracks", () => {
  expect(destinationLatlon(0, 0, 90, Math.PI * 6371 / 180)[1]).toBeCloseTo(1, 8);
  expect(destinationLatlon(0, 0, 0, Math.PI * 6371 / 180)[0]).toBeCloseTo(1, 8);
  expect(destinationLatlon(0, 0, 90, -Math.PI * 6371 / 180)[1]).toBeCloseTo(-1, 8);
  expect(destinationLatlon(0, 179.9, 90, 50)[1]).toBeLessThan(-179);
  for (const position of [
    { lat: 0.1, lon: 0, heading_deg: 0 },
    { lat: 15, lon: 179.9, heading_deg: 90 },
    { lat: 89.8, lon: 179.9, heading_deg: 0 },
  ]) {
    const course = predictSkyCourse({ ...courseAircraft, ...position }, { ...courseStation, ...position });
    expect(course.unavailableReason).toBeNull();
    for (const point of course.points) {
      expect([point.az, point.el, point.seconds].every(Number.isFinite)).toBe(true);
      const radius = 192 * (1 - point.el / 90);
      expect(radius).toBeGreaterThanOrEqual(0);
      expect(radius).toBeLessThanOrEqual(192);
    }
  }
});

test("missing or invalid course data has an explicit unavailable reason", () => {
  for (const invalid of [
    { heading_deg: null }, { heading_deg: NaN },
    { velocity_kts: null }, { velocity_kts: 0 }, { velocity_kts: -1 }, { velocity_kts: Infinity },
    { lat: NaN }, { lat: 91 }, { lon: 181 }, { altitude_ft: NaN }, { altitude_ft: 0 },
  ]) {
    const course = predictSkyCourse({ ...courseAircraft, ...invalid }, courseStation);
    expect(course.points).toEqual([]);
    expect(course.unavailableReason).toBeTruthy();
  }
  for (const station of [null, { ...courseStation, altitudeM: NaN }, { ...courseStation, lat: 91 }]) {
    const course = predictSkyCourse(courseAircraft, station);
    expect(course.points).toEqual([]);
    expect(course.unavailableReason).toContain("station");
  }
  const farAway = predictSkyCourse({ ...courseAircraft, lon: -90 }, courseStation);
  expect(farAway.points).toEqual([]);
  expect(farAway.unavailableReason).toContain("horizon");
});

const skyStationLocator = "JJ00aa";
const [skyLat, skyLon] = locatorToLatlon(skyStationLocator);
function skyAircraft(icao24: string, heading = 90, lat = skyLat + 0.03, lon = skyLon - 0.2) {
  return {
    ...courseAircraft, icao24, callsign: `FLIGHT${icao24}`, heading_deg: heading, lat, lon,
    ...scatterGeometry(skyLat, skyLon, skyLat + 1, skyLon + 1, lat, lon, 33000 * 0.3048),
  };
}

async function skyEvent(page: Page, event: string, data: Record<string, unknown>) {
  await page.evaluate(({ event, data }) => {
    const emit = Reflect.get(window, "__tauriMockEmit") as (event: string, payload: string) => void;
    emit("sidecar-message", JSON.stringify({ event, data }));
  }, { event, data });
}

async function skyFeed(page: Page, aircraft: unknown[], station: string | null = skyStationLocator) {
  await skyEvent(page, "airscout_update", {
    aircraft, total: aircraft.length, station_a: station, station_b: "JJ11",
    data_freshness: "fresh", last_update_epoch_s: Math.floor(Date.now() / 1000),
  });
}

async function openSky(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.showSkyDome", "true");
    localStorage.setItem("airwsjtx.portableViewMode", "hidden");
  });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Rig", exact: true })).toBeVisible();
}

test("selecting an aircraft shows one course that updates, switches and clears on unlock", async ({ page }) => {
  await openSky(page);
  const first = skyAircraft("abc123");
  const second = skyAircraft("def456", 45, skyLat - 0.2, skyLon);
  await skyFeed(page, [first, second]);
  const dome = page.locator(".skydome-container");
  await expect(dome.locator("[data-aircraft-course]")).toHaveCount(0);
  await dome.getByRole("button", { name: "Lock FLIGHTabc123", exact: true }).click();
  await expect.poll(() => page.evaluate(() => Reflect.get(window, "__mockLockRequest"))).toBe("abc123");
  await skyEvent(page, "plane_locked", { ...first, state: "tracking" });
  const course = dome.locator('[data-aircraft-course="abc123"]');
  await expect(course).toBeVisible();
  await expect(dome.locator("[data-aircraft-course]")).toHaveCount(1);
  await expect(dome.locator("svg > defs > marker")).toHaveCount(1);
  await expect(dome.locator("svg > defs > clipPath")).toHaveCount(1);
  const future = course.locator('polyline[aria-label="Predicted future course"]');
  await expect(future).toHaveAttribute("stroke-dasharray", "5 4");
  await expect(future).toHaveCSS("stroke", "rgb(88, 166, 255)");
  await expect(course.locator("line[marker-end]")).toHaveCount(1);
  for (let minute = 1; minute <= 5; minute++) {
    await expect(course.getByText(`+${minute}m`, { exact: true })).toBeVisible();
  }
  await dome.screenshot({ path: test.info().outputPath("selected-course.png") });
  const points = await future.getAttribute("points");
  await skyFeed(page, [{ ...first, heading_deg: 135 }, second]);
  await expect(future).not.toHaveAttribute("points", points!);
  const approach = course.locator('polyline[aria-label="Estimated approach course"]');
  const dot = dome.locator('[data-aircraft-id="abc123"] circle').last();
  const current = (await future.getAttribute("points"))!.split(" ")[0].split(",").map(Number);
  const approachEnd = (await approach.getAttribute("points"))!.split(" ").at(-1)!.split(",").map(Number);
  expect(current).toEqual(approachEnd);
  expect(current[0]).toBeCloseTo(Number(await dot.getAttribute("cx")), 0);
  expect(current[1]).toBeCloseTo(Number(await dot.getAttribute("cy")), 0);
  await page.setViewportSize({ width: 900, height: 600 });
  const bounds = await dome.locator("svg").boundingBox();
  await dome.locator("svg").click({ position: {
    x: bounds!.width * (0.5 + 96 / 430), y: bounds!.height * 0.5,
  } });
  await expect.poll(() => page.evaluate(() => Reflect.get(window, "__mockRotatorTarget")))
    .toEqual({ azimuth: 90, elevation: 45 });
  await dome.getByRole("button", { name: "Lock FLIGHTdef456", exact: true }).press("Enter");
  await expect.poll(() => page.evaluate(() => Reflect.get(window, "__mockLockRequest"))).toBe("def456");
  await skyEvent(page, "plane_locked", { ...second, state: "tracking" });
  await expect(course).toHaveCount(0);
  await expect(dome.locator('[data-aircraft-course="def456"]')).toBeVisible();
  await skyEvent(page, "plane_unlocked", {});
  await expect(dome.locator("[data-aircraft-course]")).toHaveCount(0);
  await expect(dome).toContainText("Select an aircraft");
});

test("selected aircraft remains on the dome when outside the first 20 candidates", async ({ page }) => {
  await openSky(page);
  const aircraft = Array.from({ length: 25 }, (_, index) => skyAircraft(`abc${index}`));
  await skyFeed(page, aircraft);
  const dome = page.locator(".skydome-container");
  await expect(dome.locator('[data-aircraft-id="abc24"]')).toHaveCount(0);
  await skyEvent(page, "plane_locked", { ...aircraft[24], state: "tracking" });
  await expect(dome.locator('[data-aircraft-id="abc24"]')).toBeVisible();
  await expect(dome.locator('[data-aircraft-course="abc24"]')).toBeVisible();
  await expect(dome.locator("[data-aircraft-course]")).toHaveCount(1);
});

test("course explains missing station, motion data and vanished aircraft instead of guessing", async ({ page }) => {
  await openSky(page);
  const aircraft = skyAircraft("abc123");
  const dome = page.locator(".skydome-container");
  await skyFeed(page, [aircraft], null);
  await skyEvent(page, "plane_locked", { ...aircraft, state: "tracking" });
  await expect(dome).toContainText("Course unavailable: Set a valid MY station");
  await expect(dome.locator("[data-aircraft-course]")).toHaveCount(0);
  for (const invalid of [{ heading_deg: null }, { velocity_kts: null }, { velocity_kts: 0 }]) {
    await skyFeed(page, [{ ...aircraft, ...invalid }]);
    await expect(dome).toContainText("Course unavailable: Aircraft track or ground speed");
    await expect(dome.locator("[data-aircraft-course]")).toHaveCount(0);
  }
  await skyFeed(page, [aircraft]);
  await expect(dome.locator("[data-aircraft-course]")).toHaveCount(1);
  await skyFeed(page, []);
  await expect(dome.locator("[data-aircraft-course]")).toHaveCount(0);
  await expect(dome).toContainText("selected aircraft is no longer in the current feed");
});

test("course uses saved station altitude, not an unapplied edit", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.myAltitudeM", "100");
    localStorage.setItem("airwsjtx.myGridOverride", "JJ00aa");
    localStorage.setItem("airwsjtx.myCallOverride", "K1ABC");
  });
  await openSky(page);
  const aircraft = skyAircraft("abc123");
  await skyFeed(page, [aircraft]);
  await skyEvent(page, "plane_locked", { ...aircraft, state: "tracking" });
  const future = page.locator('polyline[aria-label="Predicted future course"]');
  const points = await future.getAttribute("points");
  await page.getByRole("textbox", { name: "MY altitude (m)", exact: true }).fill("2000");
  await expect(future).toHaveAttribute("points", points!);
  await page.getByRole("button", { name: "Apply MY station" }).click();
  await expect(future).not.toHaveAttribute("points", points!);
});

test("Sky Dome controls remain accessible and remember visibility", async ({ page }) => {
  await page.goto("/");
  const controls = page.getByRole("group", { name: "Sky Dome display controls" });
  const panel = page.getByRole("region", { name: "Sky Dome", exact: true });
  await expect(panel).toHaveCount(0);
  await expect(controls.getByRole("button", { name: "Hide", exact: true })).toHaveAttribute("aria-pressed", "true");
  await controls.getByRole("button", { name: "Show", exact: true }).click();
  await expect(panel).toBeVisible();
  await expect(panel.locator("xpath=..")).toHaveClass(/display-panels/);
  const dome = panel.locator("svg");
  for (const [elevation, radius] of [[22.5, 144], [45, 96], [67.5, 48]]) {
    await expect(dome.getByText(`${elevation}°`, { exact: true })).toBeVisible();
    await expect(dome.locator(`circle[r="${radius}"][fill="none"]`)).toHaveCount(1);
  }
  await page.reload();
  await expect(panel).toBeVisible();
  await expect(controls.getByRole("button", { name: "Show", exact: true })).toHaveAttribute("aria-pressed", "true");
  await controls.getByRole("button", { name: "Hide", exact: true }).click();
  await page.reload();
  await expect(panel).toHaveCount(0);
  await expect(controls).toBeVisible();
  await expect(controls.getByRole("button", { name: "Hide", exact: true })).toHaveAttribute("aria-pressed", "true");
});

for (const platform of [
  { name: "Android", userAgent: "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36" },
  { name: "iOS", userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148" },
]) {
  test.describe(`Sky Dome is unavailable on ${platform.name}`, () => {
    test.use({ userAgent: platform.userAgent, viewport: { width: 390, height: 844 } });

    test("does not render even with the desktop preference enabled", async ({ page }) => {
      await page.addInitScript(() => {
        localStorage.setItem("airwsjtx.showSkyDome", "true");
      });
      await page.goto("/");
      await expect(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
      await expect(page.getByRole("group", { name: "Sky Dome display controls" })).toHaveCount(0);
      await expect(page.locator("#skydome-panel, .skydome-container")).toHaveCount(0);
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      await expect(page.getByText("Sky Dome", { exact: true })).toHaveCount(0);
    });
  });
}

for (const viewport of [
  { width: 900, height: 600 },
  { width: 1100, height: 720 },
  { width: 1440, height: 900 },
]) {
  test(`Sky Dome fits ${viewport.width}x${viewport.height} with every Az/El mode`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto("/");
    await page.getByRole("group", { name: "Sky Dome display controls" })
      .getByRole("button", { name: "Show", exact: true }).click();
    const azElControls = page.getByRole("group", { name: "Az / El display controls" });
    await azElControls.getByRole("slider", { name: "Scale" }).fill("180");
    for (const mode of ["Panel", "Overlay", "Hide"]) {
      await azElControls.getByRole("button", { name: mode, exact: true }).click();
      const panel = await page.locator("#skydome-panel").boundingBox();
      const dome = await page.locator(".skydome-container svg").boundingBox();
      const content = await page.locator(".content").boundingBox();
      const footer = await page.locator(".bottombar").boundingBox();
      expect(panel).not.toBeNull();
      expect(dome).not.toBeNull();
      expect(content).not.toBeNull();
      expect(footer).not.toBeNull();
      expect(panel!.width).toBeGreaterThan(400);
      expect(dome!.width).toBeCloseTo(dome!.height, 0);
      expect(dome!.x).toBeGreaterThanOrEqual(panel!.x);
      expect(dome!.x + dome!.width).toBeLessThanOrEqual(panel!.x + panel!.width);
      expect(dome!.y + dome!.height).toBeLessThanOrEqual(panel!.y + panel!.height);
      expect(content!.height).toBeGreaterThanOrEqual(160);
      expect(footer!.y + footer!.height).toBeLessThanOrEqual(viewport.height);
      expect(await page.locator(".display-panels").evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
      if (mode === "Panel") {
        expect(await page.locator(".portable-panel").evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
      }
    }
  });
}

test("pointing uses SVG coordinates after resizing", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("group", { name: "Sky Dome display controls" })
    .getByRole("button", { name: "Show", exact: true }).click();
  const dome = page.locator(".skydome-container svg");
  for (const viewport of [{ width: 1440, height: 900 }, { width: 900, height: 600 }]) {
    await page.setViewportSize(viewport);
    const bounds = await dome.boundingBox();
    expect(bounds).not.toBeNull();
    await dome.click({ position: {
      x: bounds!.width * (0.5 + 96 / 430),
      y: bounds!.height * 0.5,
    } });
    await expect.poll(() => page.evaluate(() => Reflect.get(window, "__mockRotatorTarget")))
      .toEqual({ azimuth: 90, elevation: 45 });
    await dome.click({ position: {
      x: bounds!.width * 0.5,
      y: bounds!.height * (0.5 + 96 / 430),
    } });
    await expect.poll(() => page.evaluate(() => Reflect.get(window, "__mockRotatorTarget")))
      .toEqual({ azimuth: 180, elevation: 45 });
  }
});
