# Railway construye con el Dockerfile cuando existe, por lo que este Procfile
# normalmente NO se usa. Se conserva apuntando al MISMO script de arranque para
# que, si alguna vez se cambia el constructor a Railpack o se despliega en una
# plataforma que lea Procfile, el comando de producción sea idéntico. Dos
# comandos de arranque distintos fueron precisamente la causa de que el
# servidor de desarrollo llegara a producción.
web: ./scripts/arrancar.sh
