/* Station conf parser (plan 5.6). Pure: text in, struct out, no I/O. */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "addr.h"
#include "conf.h"

#define LINE_MAX_LEN 256
#define TOK_MAX 16
#define NAME_MAX_LEN 7   /* node ids fit in nic_hello_t.node (8 bytes with NUL) */

typedef struct { conf_t *c; char *err; size_t errlen; int line; } parse_t;

static int fail(parse_t *p, const char *msg) {
    snprintf(p->err, p->errlen, "line %d: %s", p->line, msg);
    return -1;
}

/* 0 ok; -1 empty or not all digits. */
static int num(const char *s, unsigned long *out) {
    char *end;
    if (*s < '0' || *s > '9') return -1;
    *out = strtoul(s, &end, 10);
    return *end ? -1 : 0;
}

/* iface IDX lan N port N mac M ip CIDR. Fields may come in any order; all four are required. */
static int parse_iface(parse_t *p, char **tok, int n) {
    conf_t *c = p->c;
    unsigned long idx, v;
    if (n != 10 || num(tok[1], &idx) != 0) return fail(p, "want: iface IDX lan N port N mac M ip CIDR");
    if (idx >= MAX_IFACES) return fail(p, "iface index out of range");
    if ((int)idx != c->nifaces) return fail(p, "iface lines must be numbered 0, 1, ... in order");

    conf_iface_t *f = &c->ifaces[c->nifaces];
    int seen = 0;   /* one bit per field, all four needed */
    for (int k = 2; k < n; k += 2) {
        const char *key = tok[k], *val = tok[k + 1];
        if (strcmp(key, "lan") == 0 && num(val, &v) == 0) { f->lan = (int)v; seen |= 1; }
        else if (strcmp(key, "port") == 0 && num(val, &v) == 0 && v <= 65535) { f->port = (int)v; seen |= 2; }
        else if (strcmp(key, "mac") == 0 && mac_parse(val, f->mac) == 0) seen |= 4;
        else if (strcmp(key, "ip") == 0 && cidr_parse(val, &f->ip, &f->prefix) == 0) seen |= 8;
        else return fail(p, "bad or unknown field in iface line");
    }
    if (seen != 15) return fail(p, "iface line needs lan, port, mac and ip");
    c->nifaces++;
    return 0;
}

/* proxy IFACE CIDR: the iface must already be defined (plan 5.6). */
static int parse_proxy(parse_t *p, char **tok, int n) {
    conf_t *c = p->c;
    unsigned long idx;
    uint32_t net;
    uint8_t prefix;
    if (n != 3 || num(tok[1], &idx) != 0) return fail(p, "want: proxy IFACE CIDR");
    if (idx >= (unsigned long)c->nifaces) return fail(p, "proxy before its iface");
    if (cidr_parse(tok[2], &net, &prefix) != 0) return fail(p, "bad proxy CIDR");
    conf_iface_t *f = &c->ifaces[idx];
    if (f->nproxy >= CONF_MAX_PROXY) return fail(p, "too many proxy ranges");
    f->proxy_net[f->nproxy] = net;
    f->proxy_prefix[f->nproxy++] = prefix;
    return 0;
}

/* node NAME host|router */
static int parse_node(parse_t *p, char **tok, int n) {
    conf_t *c = p->c;
    if (n != 3) return fail(p, "want: node NAME host|router");
    if (c->node[0]) return fail(p, "second node line");
    if (strlen(tok[1]) > NAME_MAX_LEN) return fail(p, "node id longer than 7 characters");
    if (strcmp(tok[2], "host") == 0) c->is_router = 0;
    else if (strcmp(tok[2], "router") == 0) c->is_router = 1;
    else return fail(p, "kind must be host or router");
    snprintf(c->node, sizeof c->node, "%s", tok[1]);
    return 0;
}

/* route CIDR via IP. The next hop is checked for on-link by the caller, which knows the ifaces. */
static int parse_route(parse_t *p, char **tok, int n) {
    conf_t *c = p->c;
    if (n != 4 || strcmp(tok[2], "via") != 0) return fail(p, "want: route CIDR via IP");
    if (c->nroutes >= CONF_MAX_ROUTES) return fail(p, "too many route lines");
    conf_route_t *r = &c->routes[c->nroutes];
    uint8_t prefix;
    if (cidr_parse(tok[1], &r->net, &prefix) != 0) return fail(p, "bad route CIDR");
    r->prefix = prefix;
    if (ip_parse(tok[3], &r->via) != 0) return fail(p, "bad route next hop");
    c->nroutes++;
    return 0;
}

/* arp ttl_ms N retry_ms N retries N. Any subset; the rest keep their defaults. */
static int parse_arp(parse_t *p, char **tok, int n) {
    if (n % 2 == 0) return fail(p, "want: arp ttl_ms N retry_ms N retries N");
    for (int k = 1; k < n; k += 2) {
        unsigned long v;
        if (num(tok[k + 1], &v) != 0) return fail(p, "arp values must be numbers");
        if (strcmp(tok[k], "ttl_ms") == 0) p->c->ttl_ms = v;
        else if (strcmp(tok[k], "retry_ms") == 0) p->c->retry_ms = v;
        else if (strcmp(tok[k], "retries") == 0) p->c->retries = (int)v;
        else return fail(p, "unknown arp keyword");
    }
    return 0;
}

static int parse_gateway(parse_t *p, char **tok, int n) {
    if (n != 2 || ip_parse(tok[1], &p->c->gateway) != 0) return fail(p, "want: gateway IP");
    p->c->has_gateway = 1;
    return 0;
}

/* Splits one line into tokens, then dispatches on the keyword. */
static int parse_line(parse_t *p, char *line) {
    char *tok[TOK_MAX], *save = NULL;
    int n = 0;
    for (char *t = strtok_r(line, " \t\r", &save); t; t = strtok_r(NULL, " \t\r", &save)) {
        if (n == TOK_MAX) return fail(p, "too many fields");
        tok[n++] = t;
    }
    if (n == 0) return 0;                            /* blank or comment-only line */
    if (strcmp(tok[0], "node") == 0)    return parse_node(p, tok, n);
    if (strcmp(tok[0], "iface") == 0)   return parse_iface(p, tok, n);
    if (strcmp(tok[0], "proxy") == 0)   return parse_proxy(p, tok, n);
    if (strcmp(tok[0], "route") == 0)   return parse_route(p, tok, n);
    if (strcmp(tok[0], "gateway") == 0) return parse_gateway(p, tok, n);
    if (strcmp(tok[0], "arp") == 0)     return parse_arp(p, tok, n);
    return fail(p, "unknown keyword");
}

int conf_parse(const char *text, conf_t *c, char *err, size_t errlen) {
    memset(c, 0, sizeof *c);
    c->ttl_ms = 30000; c->retry_ms = 1000; c->retries = 0;   /* same defaults as net_init; 0 = retry forever */
    parse_t p = { c, err, errlen, 0 };
    char line[LINE_MAX_LEN];
    const char *s = text;
    while (*s) {
        const char *nl = strchr(s, '\n');
        size_t len = nl ? (size_t)(nl - s) : strlen(s);
        p.line++;
        if (len >= sizeof line) return fail(&p, "line too long");
        memcpy(line, s, len);
        line[len] = '\0';
        char *hash = strchr(line, '#');
        if (hash) *hash = '\0';                       /* comments run to end of line */
        if (parse_line(&p, line) != 0) return -1;
        s += len + (nl ? 1 : 0);
    }
    if (!c->node[0]) { snprintf(err, errlen, "missing node line"); return -1; }
    if (c->nifaces == 0) { snprintf(err, errlen, "missing iface line"); return -1; }
    return 0;
}
