#!/bin/sh
# Install output policy before starting any untrusted extractor; fail closed.
set -eu
proxy=$(getent ahostsv4 warp | awk 'NR==1{print $1}')
test -n "$proxy"
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -p udp -d 127.0.0.11 --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp -d 127.0.0.11 --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp -d "$proxy" -m multiport --dports 1080,4416 -j ACCEPT
for net in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.0.2.0/24 192.88.99.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 224.0.0.0/4 240.0.0.0/4; do
    iptables -A OUTPUT -d "$net" -j REJECT
done
ip6tables -P OUTPUT DROP
mkdir -p /run/mewvia /data/tmp
chown 10001:10001 /run/mewvia /data/tmp
exec setpriv --reuid=10001 --regid=10001 --clear-groups --no-new-privs python -m app.media.worker_service
