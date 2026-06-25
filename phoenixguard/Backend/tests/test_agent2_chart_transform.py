from phoenixguard.vision.v3_chart_transform import V3ChartTransform


def test_chart_transform_exists_for_chart_state():
    chart_size = [800, 600]
    t = V3ChartTransform.create(chart_size, frame_id=12345)
    assert t.chart_transform_id.startswith("ct_")
    assert t.frame_id == 12345
    assert t.chart_image_bounds[2] == 800
    assert t.chart_image_bounds[3] == 600


def test_normalized_to_chart_image_conversion():
    chart_size = [200, 100]
    t = V3ChartTransform.create(chart_size, frame_id=1)
    norm = [0.0, 0.0, 1.0, 1.0]
    px = t.normalized_to_chart_image(norm)
    assert px == [0, 0, 200, 100]


def test_chart_image_to_screen_conversion():
    chart_size = [300, 150]
    t = V3ChartTransform.create(chart_size, frame_id=2)
    px = [10, 10, 100, 50]
    scr = t.chart_image_to_screen(px)
    assert scr == [10, 10, 100, 50]
