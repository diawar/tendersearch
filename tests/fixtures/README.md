Browser fixtures contain synthetic data only. They are rendered from the current
Django templates without reading application data or calling external APIs:

```sh
python manage.py shell -c "exec(open('tests/render_browser_fixtures.py').read())"
```

Run this from the repository root in the Django development environment after
changing preparation/calculator templates. Commit the regenerated HTML alongside
the change so browser tests also run from a fresh checkout. Browser screenshots
and runtime logs go to the ignored `data/` directory.
