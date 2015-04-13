(python ampcrowd/manage.py sqlclear basecrowd amt internal results_dashboard | sed 's/";/" CASCADE;/' | python ampcrowd/manage.py dbshell) && python ampcrowd/manage.py syncdb --noinput
