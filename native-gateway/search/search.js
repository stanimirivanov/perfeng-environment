'use strict';

document.getElementById('search').addEventListener('click', () => {
  const items = ['Fixture result one', 'Fixture result two', 'Fixture result three'];
  const list = document.createElement('ul');
  list.id = 'result-items';
  for (const text of items) {
    const item = document.createElement('li');
    item.textContent = text;
    list.append(item);
  }
  const results = document.getElementById('results');
  results.replaceChildren(list);
  results.hidden = false;
});
