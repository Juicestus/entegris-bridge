with open('all.txt', 'r') as f:
    txt = f.read()

    tests = txt.split("Test")
    for test in tests:
        if len(test) == 0: continue
        i = test.index('\n')
        header = test[:i].strip()
        body = test[i:].strip()
        fn = 'test' + header.replace(' ', '').lower() + '.out'
        with open(fn, 'w') as g:
            g.write(body)
