FROM docker.chabokan.net/python:3.9-buster

ENV TZ=Asia/Tehran
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
WORKDIR /app

RUN apt-get update && apt-get -y install pigz duc default-mysql-client unar nano vim htop net-tools zip unzip iputils-ping \
&& mkdir /backups && mkdir /builds

RUN wget https://s3.ir-thr-at1.arvanstorage.ir/public-chabok/docker-latest.tgz && tar -xvzf docker-latest.tgz && mv docker/* /usr/bin/

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

ADD start.sh /
RUN chmod +x /start.sh
EXPOSE 80

CMD ["/start.sh"]
