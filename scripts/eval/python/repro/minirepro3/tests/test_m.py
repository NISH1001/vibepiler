import lib.views


def test_kw(app):
    class Local1(lib.views.View):
        pass

    app.add(Local1.as_view("x"))


def test_direct():
    class Local2(lib.views.View):
        pass

    return Local2.as_view("y")
