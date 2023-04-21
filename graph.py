import kahan
import math
import random
import statistics
import sys
import follow

try:
    import scipy.special
except ImportError:
    scipy = None
try:
    import numpy as np
except ImportError:
    np = None


class Integral:
    """Caches the samples to evalute the integral several times
        """

    def __init__(self,
                 data_given_theta,
                 theta_given_psi,
                 method="metropolis",
                 **options):
        """data_given_theta : callable
                      the join probability of the observed data viewed
                      as a function of parameter
                theta_given_psi : callable
                      conditional probability of parameters theta given
                      hyper-parameter psi
                method : str or callable, optional
                      the type of the sampling algorithm to sample from
                      data_given_theta. Can be one of

                      - metropolis (default)
                      - langevin
                      - tmcmc
                      - korali (WIP)
                      - hamiltonian (WIP)

                options : dict, optional
                      a dictionary of options for the sampling algorithm"""
        self.theta_given_psi = theta_given_psi
        if hasattr(self.theta_given_psi, '_f'):
            follow.Stack.append(self.theta_given_psi._f)

        if method == "metropolis":
            self.samples = list(metropolis(data_given_theta, **options))
        elif method == "langevin":
            self.samples = list(langevin(data_given_theta, **options))
        elif method == "tmcmc":
            self.samples = tmcmc(data_given_theta, **options)
        elif method == "korali":
            self.samples = korali_sample(data_given_theta, **options)
        else:
            raise ValueError("Unknown sampler '%s'" % method)

        if hasattr(self.theta_given_psi, '_f'):
            follow.Stack.pop()

    def __call__(self, psi):
        """Estimate the integral given hyproparameter psi"""
        return statistics.fmean(
            self.theta_given_psi(theta, psi) for theta in self.samples)


def metropolis(fun, draws, init, scale, log=False):
    """Metropolis sampler

        Parameters
        ----------
        fun : callable
              the unnormalized density or the log unnormalized probability
              (see log)
        draws : int
              the number of samples to draw
        init : tuple
              the initial point
        scale : tuple
              the scale of the proposal distribution (same size as init)
        log : bool
              set True to assume log-probability (default: False)

        Return
        ----------
        samples : list
              list of samples"""

    def flin(pp, p):
        return pp > random.uniform(0, 1) * p

    def flog(pp, p):
        return pp > math.log(random.uniform(0, 1)) + p

    x = init[:]
    p = fun(x)
    t = 0
    accept = 0
    cond = flog if log else flin
    while True:
        yield x
        t += 1
        if t == draws:
            break
        xp = tuple(e + random.gauss(0, s) for e, s in zip(x, scale))
        pp = fun(xp)
        if pp > p or cond(pp, p):
            x, p = xp, pp
            accept += 1
    sys.stderr.write("graph.metropolis: accept = %g\n" % (accept / draws))


def langevin(fun, draws, init, dfun, sigma, log=False):
    """Metropolis-adjusted Langevin (MALA) sampler

        Parameters
        ----------
        fun : callable
              the unnormalized density or the log unnormalized probability
              (see log)
        draws : int
              the number of samples to draw
        init : tuple
              the initial point
        h : float
              the step of the proposal
        dfun : callable
              the log unnormalized probability
        log : bool
              set True to assume log-probability (default: False)

        Return
        ----------
        samples : list
              list of samples"""

    def flin(pp, p, d, dp):
        a = pp * math.exp(d)
        b = p * math.exp(dp)
        return a > b or a > random.uniform(0, 1) * b

    def flog(pp, p, d, dp):
        a = pp + d
        b = p + dp
        return a > b or a > math.log(random.uniform(0, 1)) + b

    def sqdiff(a, b):
        return kahan.sum((a - b)**2 for a, b in zip(a, b))

    s2 = sigma * sigma
    x = init[:]
    y = tuple(x + s2 / 2 * d for x, d in zip(x, dfun(x)))
    p = fun(x)
    t = 0
    accept = 0
    cond = flog if log else flin
    while True:
        yield x
        t += 1
        if t == draws:
            break
        xp = tuple(random.gauss(y, sigma) for y in y)
        yp = tuple(xp + s2 / 2 * d for xp, d in zip(xp, dfun(xp)))
        pp = fun(xp)
        if cond(pp, p, sqdiff(xp, y) / (2 * s2), sqdiff(x, yp) / (2 * s2)):
            x, p, y = xp, pp, yp
            accept += 1
    sys.stderr.write("graph.langevin: accept = %g\n" % (accept / draws))


def tmcmc(fun, draws, lo, hi, beta=1, return_evidence=False, trace=False):
    """TMCMC sampler

        Parameters
        ----------
        fun : callable
               log-probability
        draws : int
              the number of samples to draw
        init : tuple
              the initial point
        lo, hi : tuples
              the bounds of the initial distribution
        beta : float
              the coefficient to scale the proposal distribution
              (default: 1)
        return_evidence : bool
              if true returns a tuple (samples, evidence) (default:
              False)
        trace : bool
              return a trace of the algorithm (default: False)
        Return
        ----------
        samples : list
               a list of samples"""

    def inside(x):
        for l, h, e in zip(lo, hi, x):
            if e < l or e > h:
                return False
        return True

    if scipy == None:
        raise ModuleNotFoundError("tmcm needs scipy")
    if np == None:
        raise ModuleNotFoundError("tmcm needs nump")
    betasq = beta * beta
    eps = 1e-6
    p = 0
    S = 0
    d = len(lo)
    x = [
        tuple(random.uniform(l, h) for l, h in zip(lo, hi))
        for i in range(draws)
    ]
    f = np.array([fun(e) for e in x])
    x2 = [[None] * d for i in range(draws)]
    sigma = [[None] * d for i in range(d)]
    f2 = np.empty_like(f)
    End = False
    Trace = []
    accept = draws
    while True:
        if trace:
            Trace.append((x[:], accept))
        if End == True:
            return Trace if trace else (x, S) if return_evidence else x
        old_p, plo, phi = p, p, 2
        while phi - plo > eps:
            p = (plo + phi) / 2
            temp = (p - old_p) * f
            M1 = scipy.special.logsumexp(temp) - math.log(draws)
            M2 = scipy.special.logsumexp(2 * temp) - math.log(draws)
            if M2 - 2 * M1 > math.log(2):
                phi = p
            else:
                plo = p
        if p > 1:
            p = 1
            End = True
        dp = p - old_p
        S += scipy.special.logsumexp(dp * f) - math.log(draws)
        weight = scipy.special.softmax(dp * f)
        mu = [kahan.sum(w * e[k] for w, e in zip(weight, x)) for k in range(d)]
        x0 = [[a - b for a, b in zip(e, mu)] for e in x]
        for l in range(d):
            for k in range(l, d):
                sigma[k][l] = sigma[l][k] = betasq * kahan.sum(
                    w * e[k] * e[l] for w, e in zip(weight, x0))
        ind = random.choices(range(draws),
                             cum_weights=list(kahan.cumsum(weight)),
                             k=draws)
        ind.sort()
        sqrtC = np.real(scipy.linalg.sqrtm(sigma))
        accept = 0
        for i, j in enumerate(ind):
            delta = [random.gauss(0, 1) for k in range(d)]
            xp = tuple(a + b for a, b in zip(x[j], sqrtC @ delta))
            if inside(xp):
                fp = fun(xp)
                if fp > f[j] or p * fp > p * f[j] + math.log(
                        random.uniform(0, 1)):
                    x[j] = xp[:]
                    f[j] = fp
                    accept += 1
            x2[i] = x[j][:]
            f2[i] = f[j]
        x2, x, f2, f = x, x2, f, f2


def cmaes(fun, x0, sigma, g_max, trace=False):
    """CMA-ES optimization

        Parameters
        ----------
        fun : callable
              a target function
        x0 : tuple
              the initial point
        sigma : double
              initial variance
        g_max : int
              maximum generation
        trace : bool
              return a trace of the algorithm (default: False)

        Return
        ----------
        xmin : tuple"""

    def cumulation(c, A, B):
        alpha = 1 - c
        beta = math.sqrt(c * (2 - c) * mueff)
        return [alpha * a + beta * b for a, b in zip(A, B)]

    def wsum(A):
        return [
            math.fsum(w * a[i] for w, a in zip(weights, A)) for i in range(N)
        ]

    if scipy == None:
        raise ModuleNotFoundError("cmaes needs scipy")
    if np == None:
        raise ModuleNotFoundError("cmaes needs nump")
    xmean, N = x0[:], len(x0)
    lambd = 4 + int(3 * math.log(N))
    mu = lambd // 2
    weights = [math.log((lambd + 1) / 2) - math.log(i + 1) for i in range(mu)]
    weights = [e / math.fsum(weights) for e in weights]
    mueff = 1 / math.fsum(e**2 for e in weights)
    cc = (4 + mueff / N) / (N + 4 + 2 * mueff / N)
    cs = (mueff + 2) / (N + mueff + 5)
    c1 = 2 / ((N + 1.3)**2 + mueff)
    cmu = min(1 - c1, 2 * (mueff - 2 + 1 / mueff) / ((N + 2)**2 + mueff))
    damps = 1 + 2 * max(0, math.sqrt((mueff - 1) / (N + 1)) - 1) + cs
    chiN = math.sqrt(2) * math.gamma((N + 1) / 2) / math.gamma(N / 2)
    ps, pc, C = [0] * N, [0] * N, np.identity(N)
    Trace = []
    for gen in range(1, g_max + 1):
        sqrtC = np.real(scipy.linalg.sqrtm(C))
        x0 = [[random.gauss(0, 1) for d in range(N)] for i in range(lambd)]
        x1 = [sqrtC @ e for e in x0]
        xs = [xmean + sigma * e for e in x1]
        ys = [fun(e) for e in xs]
        ys, x0, x1, xs = zip(*sorted(zip(ys, x0, x1, xs)))
        xmean = wsum(xs)
        ps = cumulation(cs, ps, wsum(x0))
        pssq = math.fsum(e**2 for e in ps)
        sigma *= math.exp(cs / damps * (math.sqrt(pssq) / chiN - 1))
        Cmu = sum(w * np.outer(d, d) for w, d in zip(weights, x1))
        if (N + 1) * pssq < 2 * N * (N + 3) * (1 - (1 - cs)**(2 * gen)):
            pc = cumulation(cc, pc, wsum(x1))
            C1 = np.outer(pc, pc)
            C = (1 - c1 - cmu) * C + c1 * C1 + cmu * Cmu
        else:
            pc = [(1 - cc) * e for e in pc]
            C1 = np.outer(pc, pc)
            C = (1 - c1 - cmu) * C + c1 * (C1 + cc * (2 - cc) * C) + cmu * Cmu
        if trace:
            Trace.append(
                (gen * lambd, ys[0], xs[0], sigma, C, ps, pc, Cmu, C1, xmean))
    return Trace if trace else xmean
