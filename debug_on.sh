#!/bin/bash

source /root/.profile

iptables -t filter -F DOCKER-USER
iptables -t filter -F INPUT
iptables -A DOCKER-USER -j RETURN
