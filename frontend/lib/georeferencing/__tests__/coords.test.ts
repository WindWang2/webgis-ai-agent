import { describe, expect, it } from 'vitest';
import {
  computeFitTransform,
  containerToImagePixel,
  imagePixelToContainer,
  residualArrowEnd,
  zoomAroundPoint,
} from '../coords';

describe('georeferencing coords transforms', () => {
  it('inverts containerToImagePixel <-> imagePixelToContainer under fit transform', () => {
    const image = { width: 1000, height: 500 };
    const viewport = { width: 524, height: 324 };
    const tf = computeFitTransform(image, viewport, 12);
    expect(tf.fitScale).toBeCloseTo(0.5, 6);
    expect(tf.panX).toBeCloseTo(12, 6);
    expect(tf.panY).toBeCloseTo(37, 6);

    const pt = { px: 256.5, py: 128.25 };
    const css = imagePixelToContainer(pt.px, pt.py, tf);
    const back = containerToImagePixel(css.x, css.y, tf, image);
    expect(back.inside).toBe(true);
    expect(back.px).toBeCloseTo(pt.px, 2);
    expect(back.py).toBeCloseTo(pt.py, 2);
  });

  it('keeps anchor image pixel invariant when zooming around cursor point', () => {
    const image = { width: 800, height: 600 };
    const viewport = { width: 424, height: 324 };
    const tf0 = computeFitTransform(image, viewport, 12);
    const anchorX = 185;
    const anchorY = 142;

    const beforePx = containerToImagePixel(anchorX, anchorY, tf0, image);
    const tf1 = zoomAroundPoint(tf0, 4.0, anchorX, anchorY);
    const afterPx = containerToImagePixel(anchorX, anchorY, tf1, image);

    expect(afterPx.px).toBeCloseTo(beforePx.px, 2);
    expect(afterPx.py).toBeCloseTo(beforePx.py, 2);

    // High zoom clamps cleanly inside bounds on extreme corners
    const out = containerToImagePixel(-999, 99999, tf1, image);
    expect(out.inside).toBe(false);
    expect(out.px).toBe(0);
    expect(out.py).toBe(image.height);
  });

  it('clamps exaggerated residual arrow length without NaN', () => {
    const end = residualArrowEnd(100, 100, 500, 0, 1, 10, 40);
    expect(end.x2).toBeCloseTo(140, 6);
    expect(end.y2).toBeCloseTo(100, 6);
  });
});
